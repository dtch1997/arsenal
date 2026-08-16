"""desk core — aggregate collectors into one inbox, render markdown, digest,
and sync (diff against last state, flare newly-appearing items)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import flare

from .collectors import (
    collect_concierge,
    collect_flares,
    collect_markers,
    collect_prs,
)
from .config import Config, load_config

# Section order: worker-blocking first, then review debt, notes, and self-reports.
KIND_ORDER = ["concierge", "prs", "markers", "flares"]
KIND_TITLES = {
    "concierge": "Concierge (blocked / failed tasks)",
    "prs": "Pull requests awaiting review",
    "markers": "Marker files (BLOCKED-ON-DANIEL)",
    "flares": "Recent flares (warn / page)",
}


def _home() -> Path:
    return Path(os.environ.get("DESK_HOME") or Path.home())


def desk_dir() -> Path:
    return _home() / ".desk"


def inbox_path() -> Path:
    return desk_dir() / "inbox.md"


def state_path() -> Path:
    return desk_dir() / "state.json"


@dataclass
class Inbox:
    items: list[dict]
    warnings: dict[str, list[str]]
    generated_at: datetime

    def by_kind(self, kind: str) -> list[dict]:
        return [i for i in self.items if i["kind"] == kind]


def _sev_rank(item: dict) -> int:
    """Higher = more severe. Only flares carry a severity (in their title)."""
    title = item.get("title", "").lower()
    if title.startswith("[page]"):
        return 2
    if title.startswith("[warn]"):
        return 1
    return 0


def _sort_key(item: dict):
    # most severe first, then oldest first; id as a stable tiebreaker
    return (-_sev_rank(item), -item["age_days"], item["id"])


def collect(cfg: Config | None = None, *, now: datetime | None = None,
            gh_runner=None) -> Inbox:
    """Run every collector and return a sorted :class:`Inbox`."""
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)

    results = {
        "concierge": collect_concierge(cfg, now),
        "prs": collect_prs(cfg, now, runner=gh_runner),
        "markers": collect_markers(cfg, now),
        "flares": collect_flares(cfg, now),
    }
    items, warnings = [], {}
    for kind, (kind_items, kind_warnings) in results.items():
        items.extend(kind_items)
        if kind_warnings:
            warnings[kind] = kind_warnings
    items.sort(key=_sort_key)
    return Inbox(items=items, warnings=warnings, generated_at=now)


def render_markdown(inbox: Inbox) -> str:
    lines = ["# desk — waiting on Daniel", ""]
    total = len(inbox.items)
    lines.append(f"**{total}** item{'s' if total != 1 else ''} blocked on you.")
    lines.append("")

    for kind in KIND_ORDER:
        section = inbox.by_kind(kind)
        lines.append(f"## {KIND_TITLES[kind]} ({len(section)})")
        if not section:
            lines.append("")
            lines.append("_none_")
            lines.append("")
            continue
        lines.append("")
        for item in section:
            link = f" ({item['link']})" if item.get("link") else ""
            lines.append(
                f"- **{item['age_days']:.0f}d** — {item['title']} — "
                f"{item['detail']}{link}"
            )
        lines.append("")

    lines.append("---")
    if inbox.warnings:
        lines.append("")
        lines.append("**Collector warnings:**")
        for kind in KIND_ORDER:
            for w in inbox.warnings.get(kind, []):
                lines.append(f"- {w}")
    lines.append("")
    lines.append(f"_generated {inbox.generated_at.isoformat()}_")
    lines.append("")
    return "\n".join(lines)


def render(cfg: Config | None = None, *, now: datetime | None = None,
           gh_runner=None) -> Path:
    """Collect, write ``~/.desk/inbox.md``, and return its path."""
    inbox = collect(cfg, now=now, gh_runner=gh_runner)
    path = inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(inbox))
    return path


def digest(cfg: Config | None = None, *, now: datetime | None = None,
           gh_runner=None) -> str:
    """A short plaintext summary: counts per kind + the oldest three items."""
    inbox = collect(cfg, now=now, gh_runner=gh_runner)
    counts = {k: len(inbox.by_kind(k)) for k in KIND_ORDER}
    total = len(inbox.items)
    parts = [f"desk: {total} waiting on Daniel"]
    parts.append("  " + ", ".join(f"{k}={counts[k]}" for k in KIND_ORDER))
    oldest = sorted(inbox.items, key=lambda i: -i["age_days"])[:3]
    if oldest:
        parts.append("  oldest:")
        for item in oldest:
            parts.append(f"    - [{item['age_days']:.0f}d] {item['title']}")
    return "\n".join(parts)


def _load_state() -> set[str]:
    path = state_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data.get("ids", []))


def _write_state(ids: set[str], now: datetime) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"ids": sorted(ids), "updated_at": now.isoformat()}, indent=2
    ))


def sync(cfg: Config | None = None, *, now: datetime | None = None,
         gh_runner=None) -> dict:
    """Render, then flare each newly-appearing item and update state.

    Returns ``{"path", "new", "flared"}``. New = items whose id was not in the
    previous state; each fires one ``flare.send(sev="warn", source="desk")``.
    Removed items simply drop out of the state (no flare).
    """
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    inbox = collect(cfg, now=now, gh_runner=gh_runner)

    path = inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(inbox))

    previous = _load_state()
    current_ids = {i["id"] for i in inbox.items}
    new_items = [i for i in inbox.items if i["id"] not in previous]

    flared = 0
    for item in new_items:
        flare.send(
            f"desk: {item['title']} ({item['detail']})",
            sev="warn", source="desk", now=now,
        )
        flared += 1

    _write_state(current_ids, now)
    return {"path": str(path), "new": len(new_items), "flared": flared}
