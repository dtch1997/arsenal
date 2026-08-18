"""Backend-agnostic control signals: the file-writing logic behind the
`signal_blocked` and `signal_waiting` worker tools.

The Claude backend exposes these as in-process SDK tools; the Codex backend
(and any other non-SDK backend) exposes them through the stdio MCP server in
`concierge.mcp_stdio`. Both call the exact same functions here — the mailbox
post and the atomic wait-sidecar write — so there is one implementation of the
control-channel semantics the reconciler trusts, never a fork per backend.
"""
from __future__ import annotations

import json
import os

from .records import Home, now_iso

WAIT_TIMEOUT_MINUTES = 720


def post_blocked(home: Home, tid: str, question: str) -> None:
    """signal_blocked: append the worker's question to the task mailbox. The
    reconciler sees the unanswered worker message + unmet gate and parks the
    task `blocked` until a user reply resumes the session."""
    home.post(tid, "worker", str(question), via="tool")


def write_waiting(home: Home, tid: str, *, until_shell: str, note: str,
                  attempt: int, timeout_minutes=None,
                  default_timeout: float = WAIT_TIMEOUT_MINUTES) -> dict:
    """signal_waiting: atomically write the wake-probe sidecar
    `tasks/<id>.wait.json`. The daemon owns the task record, so the worker only
    ever touches this sidecar (temp + rename, same pattern as Home.save). The
    reconciler honors it only when `attempt` matches the current attempt, then
    polls `until_shell` in the workspace and resumes the same session."""
    sidecar = {
        "until_shell": str(until_shell),
        "note": str(note),
        "timeout_minutes": float(timeout_minutes) if timeout_minutes is not None else default_timeout,
        "requested_at": now_iso(),
        "attempt": attempt,
    }
    p = home.wait_path(tid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sidecar, indent=2))
    os.replace(tmp, p)
    return sidecar
