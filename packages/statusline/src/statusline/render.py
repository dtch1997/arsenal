"""Render the Claude Code status line from the harness's stdin JSON.

Claude Code invokes the configured statusLine command on every refresh and
pipes a JSON payload on stdin (model, context_window, cost, session_name,
session_id, workspace, ...). Every line the command prints renders as its
own row in the status area (ANSI colors allowed on all lines).

Structure: LINES is a list of rows; each row is a list of segments, each a
function ``payload -> str | None``. Segments returning None are skipped,
and rows with no surviving segments are dropped entirely. Segments style
themselves (embed their own ANSI codes), except the first row, which
render() tints by remaining-context color. To extend the status line, add
a segment to an existing row or add a new row.

Current layout:

    [Fable 5] Context: 93% [██████████████░] | $3.094      <- context tint
    · what this session is about                           <- dim
    ⚑ open PR; update memory stub                          <- bold red
"""

from __future__ import annotations

from typing import Any, Callable

from statusline import session

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

BAR_WIDTH = 15
TOPIC_MAX = 80
FLAGS_MAX = 120

Segment = Callable[[dict[str, Any]], "str | None"]


def _model_name(payload: dict[str, Any]) -> str:
    return (payload.get("model") or {}).get("display_name") or "Claude"


def _remaining_pct(payload: dict[str, Any]) -> float | None:
    val = (payload.get("context_window") or {}).get("remaining_percentage")
    return None if val is None else float(val)


def _session_state(payload: dict[str, Any]) -> dict:
    return session.load(payload.get("session_id"))


def _context_color(remaining: "float | None") -> str:
    if remaining is None:
        return ""
    if remaining > 60:
        return GREEN
    if remaining > 30:
        return YELLOW
    return RED


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def model_segment(payload: dict[str, Any]) -> str:
    return f"[{_model_name(payload)}]"


def context_segment(payload: dict[str, Any]) -> str:
    remaining = _remaining_pct(payload)
    if remaining is None:
        return "Context: --%"
    filled = round(remaining * BAR_WIDTH / 100)
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    return f"Context: {remaining:.0f}% [{bar}]"


def cost_segment(payload: dict[str, Any]) -> "str | None":
    if _remaining_pct(payload) is None:
        return None
    cost = (payload.get("cost") or {}).get("total_cost_usd") or 0
    return f"| ${float(cost):.3f}"


def topic_segment(payload: dict[str, Any]) -> "str | None":
    """What the session is about: manual `claude-statusline note`, else the
    harness's auto-generated session_name."""
    topic = _session_state(payload).get("topic") or payload.get("session_name")
    if not topic:
        return None
    return f"{DIM}· {_truncate(str(topic), TOPIC_MAX)}{RESET}"


def flags_segment(payload: dict[str, Any]) -> "str | None":
    """Things to do before wrapping up (`claude-statusline flag`)."""
    flags = _session_state(payload).get("flags") or []
    if not flags:
        return None
    return f"{BOLD}{RED}⚑ {_truncate('; '.join(str(f) for f in flags), FLAGS_MAX)}{RESET}"


LINES: list[list[Segment]] = [
    [model_segment, context_segment, cost_segment],
    [topic_segment],
    [flags_segment],
]


def render(payload: dict[str, Any]) -> str:
    rows: list[str] = []
    for i, segments in enumerate(LINES):
        parts = [text for seg in segments if (text := seg(payload)) is not None]
        if not parts:
            continue
        row = " ".join(parts)
        if i == 0 and (color := _context_color(_remaining_pct(payload))):
            row = f"{color}{row}{RESET}"
        rows.append(row)
    return "\n".join(rows)
