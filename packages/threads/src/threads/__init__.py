"""threads — the bottom-up activity spine.

Every Claude session transcript is summarized (``threads scan``); the summaries
are woven onto the memory registry as threads (``threads weave``); a dashboard
renders the result (``threads serve`` / ``threads render``). The spool lives in
``~/.threads``; the registry is ``~/jarvis-memory`` (no second registry).
"""

from __future__ import annotations

# Note: submodules (threads.scan, threads.weave, …) are imported directly by
# callers; we deliberately do NOT re-export their functions here, since that
# would shadow the same-named submodules on the package namespace.

__version__ = "0.1.0"
