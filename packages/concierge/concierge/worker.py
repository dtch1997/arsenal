"""Backward-compat shim. The Claude worker moved to
`concierge.backends.claude` when pluggable backends landed (issue #60); the
daemon now selects a backend wrapper per task via `runtime.Worker.spawn`.

Kept so `python -m concierge.worker` and `from concierge import worker` (with
its private tool factories) keep working exactly as before.
"""
from __future__ import annotations

from .backends.claude import (  # noqa: F401  (re-exported surface)
    BACKEND,
    BLOCKED_TOOL,
    DELEGATE_TOOL,
    READONLY_TOOLS,
    WAIT_TIMEOUT_MINUTES,
    WAITING_TOOL,
    _blocked_tool,
    _delegate_tool,
    _normalize,
    _options,
    _waiting_tool,
    main,
    run,
)

if __name__ == "__main__":
    main()
