"""Per-session sidecar state: topic override + wrap-up flags.

The renderer and the writer CLI meet at
``~/.claude/statusline/sessions/<session_id>.json``. The renderer keys on
the ``session_id`` in the harness payload; writers (the agent, via the
Bash tool) key on the ``CLAUDE_CODE_SESSION_ID`` env var.

Schema: ``{"topic": str|null, "flags": [str]}``. Everything degrades to
"no sidecar" on any read error — the status line must never break.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATUSLINE_STATE_DIR", "~/.claude/statusline/sessions")).expanduser()

# Sidecars are per-session and sessions end; anything this old is litter.
PRUNE_AFTER_DAYS = 30


def _path(session_id: str) -> Path:
    return STATE_DIR / f"{session_id}.json"


def load(session_id: "str | None") -> dict:
    if not session_id:
        return {}
    try:
        return json.loads(_path(session_id).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save(session_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)
    _prune()


def _prune() -> None:
    cutoff = time.time() - PRUNE_AFTER_DAYS * 86400
    try:
        for f in STATE_DIR.iterdir():
            if f.suffix == ".json" and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass
