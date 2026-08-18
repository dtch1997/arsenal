"""Edition log — which PR refs appeared in which morning patch notes.

The delay-lane veto window is counted in *delivered editions*, not wall-clock
hours: a PR merges only after it has appeared in ``delay_editions`` distinct
editions. A skipped morning (weekend, broken notes cron) pauses the window
instead of letting conventions merge unseen.

Spool: ``~/.gazette/editions.jsonl`` — one line per ``gazette notes`` run,
``{"date": "YYYY-MM-DD", "refs": [...]}``. Appearances are counted over
distinct dates, so re-running notes on the same day never double-counts.
"""

from __future__ import annotations

import json
from datetime import datetime

from .config import spool_dir


def editions_path():
    return spool_dir() / "editions.jsonl"


def record_edition(now: datetime, refs: list[str]) -> None:
    path = editions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"date": now.date().isoformat(), "refs": sorted(refs)}) + "\n")
    except OSError:
        pass


def load_appearances() -> dict[str, int]:
    """ref -> number of distinct edition dates it appeared in."""
    path = editions_path()
    dates_by_ref: dict[str, set[str]] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
            date, refs = row["date"], row["refs"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        for ref in refs:
            dates_by_ref.setdefault(ref, set()).add(date)
    return {ref: len(dates) for ref, dates in dates_by_ref.items()}
