"""desk — the waiting-on-Daniel inbox.

Aggregates everything currently blocked on Daniel — blocked/failed concierge
tasks, review-stale PRs, ``BLOCKED-ON-DANIEL`` markers in memory/goals files,
and recent warn/page flares — into one markdown page, and pushes *newly
appearing* items via :mod:`flare`.

    desk render     # write ~/.desk/inbox.md
    desk digest     # short plaintext summary to stdout
    desk sync       # render + flare new items + update state
    desk serve      # serve the inbox through the lobby hub
"""

from __future__ import annotations

from .config import Config, load_config
from .core import collect, digest, render, render_markdown, sync

__all__ = [
    "collect",
    "render",
    "render_markdown",
    "digest",
    "sync",
    "load_config",
    "Config",
]

__version__ = "0.1.0"
