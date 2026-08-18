"""Render the Claude Code status line from the harness's stdin JSON.

Claude Code invokes the configured statusLine command on every refresh and
pipes a JSON payload on stdin (model, context_window, cost, workspace, ...).
Whatever the command prints (one line, ANSI colors allowed) becomes the
status line.

Structure: the line is a list of SEGMENTS, each a function
``payload -> str | None``. A segment returning None is skipped. To extend
the status line, write a segment and add it to SEGMENTS.
"""

from __future__ import annotations

from typing import Any, Callable

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

BAR_WIDTH = 15

Segment = Callable[[dict[str, Any]], "str | None"]


def _model_name(payload: dict[str, Any]) -> str:
    return (payload.get("model") or {}).get("display_name") or "Claude"


def _remaining_pct(payload: dict[str, Any]) -> float | None:
    val = (payload.get("context_window") or {}).get("remaining_percentage")
    return None if val is None else float(val)


def _context_color(remaining: float) -> str:
    if remaining > 60:
        return GREEN
    if remaining > 30:
        return YELLOW
    return RED


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


SEGMENTS: list[Segment] = [model_segment, context_segment, cost_segment]


def render(payload: dict[str, Any]) -> str:
    parts = [text for seg in SEGMENTS if (text := seg(payload)) is not None]
    line = " ".join(parts)
    remaining = _remaining_pct(payload)
    if remaining is None:
        return line
    return f"{_context_color(remaining)}{line}{RESET}"
