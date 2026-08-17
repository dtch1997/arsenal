"""threads config + spool paths.

The spool lives under ``~/.threads/`` (override the whole base with
``THREADS_HOME`` — tests and smoke runs point it at a scratch dir so they never
touch the real spool). The three *input* locations are overridable too, so
tests can point at fixture trees without monkeypatching ``Path.home``:

- ``THREADS_PROJECTS_DIR`` — the ``~/.claude/projects`` transcript tree.
- ``THREADS_MEMORY_DIR``   — the ``~/jarvis-memory`` thread registry.
- ``THREADS_CONCIERGE_HOME`` — the ``~/concierge-home`` root (for task JSONs).

Summaries are derived-but-durable: transcripts age out of ``~/.claude`` on
their own, the spool does not.
"""

from __future__ import annotations

import os
from pathlib import Path

# defaults (also documented in the README)
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MAX_CALLS = 100
DEFAULT_DORMANT_DAYS = 14
MODEL = "claude-haiku-4-5-20251001"

# rough haiku pricing (USD per token) — used only for a spend *estimate* in
# state.json when the runner does not report a real cost.
EST_USD_PER_CALL = 0.004


def _home() -> Path:
    return Path(os.environ.get("THREADS_HOME") or Path.home())


def threads_dir() -> Path:
    return _home() / ".threads"


def summaries_dir() -> Path:
    return threads_dir() / "summaries"


def candidates_dir() -> Path:
    return threads_dir() / "candidates"


def assignments_path() -> Path:
    return threads_dir() / "assignments.jsonl"


def state_path() -> Path:
    return threads_dir() / "state.json"


def projects_dir() -> Path:
    override = os.environ.get("THREADS_PROJECTS_DIR")
    return Path(override) if override else Path.home() / ".claude" / "projects"


def memory_dir() -> Path:
    override = os.environ.get("THREADS_MEMORY_DIR")
    return Path(override) if override else Path.home() / "jarvis-memory"


def concierge_home() -> Path:
    override = os.environ.get("THREADS_CONCIERGE_HOME")
    return Path(override) if override else Path.home() / "concierge-home"


def ensure_spool() -> None:
    for d in (threads_dir(), summaries_dir(), candidates_dir()):
        d.mkdir(parents=True, exist_ok=True)
