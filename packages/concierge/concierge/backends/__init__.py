"""Pluggable worker backends (issue #60).

A backend is a detached wrapper module — launched as `python -m <module>
<task_id> <attempt> [--resume <session>]` by `runtime.Worker.spawn` — that
drives some agent CLI and normalizes its event stream into the frozen
`logs/<id>/attempt-N/agent.jsonl` schema (`system`/`assistant`/`result`
events). Everything else in the daemon (runtime poll/kill, reconcile, gates,
records) is backend-agnostic: it reads only the OS process table and
agent.jsonl.

`claude` (the default, behavior-identical to the original in-daemon design)
runs a Claude Agent SDK session; `codex` drives `codex exec --json`.
"""
from __future__ import annotations

DEFAULT_BACKEND = "claude"

# backend name -> wrapper module runnable as `python -m <module>`
MODULES = {
    "claude": "concierge.backends.claude",
    "codex": "concierge.backends.codex",
}


def module_for(backend: str | None) -> str:
    """The wrapper module for a task's backend; absent/None -> the default
    (claude), which keeps legacy records that predate the `backend` field
    fully backward compatible."""
    return MODULES[backend or DEFAULT_BACKEND]
