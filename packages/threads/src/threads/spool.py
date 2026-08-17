"""Read/write the ``~/.threads`` spool: summary records, assignments, state."""

from __future__ import annotations

import json
from pathlib import Path

from . import config


def summary_path(session_id: str) -> Path:
    return config.summaries_dir() / f"{session_id}.json"


def load_summary(session_id: str) -> dict | None:
    path = summary_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_summary(record: dict) -> None:
    config.ensure_spool()
    path = summary_path(record["session_id"])
    path.write_text(json.dumps(record, indent=2))


def load_all_summaries() -> list[dict]:
    d = config.summaries_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_state() -> dict:
    path = config.state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict) -> None:
    config.ensure_spool()
    config.state_path().write_text(json.dumps(state, indent=2))


def write_assignments(rows: list[dict]) -> None:
    config.ensure_spool()
    with config.assignments_path().open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def load_assignments() -> list[dict]:
    path = config.assignments_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
