"""flare — universal distress/push channel for agents.

One always-available, push-based channel any agent process (a Claude session, a
concierge worker, a pod script, a cron job) can use to page Daniel over Slack::

    import flare
    flare.send("stuck in a toolcall loop on task X", sev="warn", source="concierge")

or from the shell (``uvx flare`` on a bare pod)::

    flare "backend is 500ing" --sev page --source pod-42

Every flare is stamped with host/cwd/branch/session/task context, always spooled
to ``~/.flare/log.jsonl``, and — if a webhook is configured — posted to Slack. It
never raises on a transport failure: a distress channel must not crash its caller.
"""

from __future__ import annotations

from .core import (
    SEVERITIES,
    config_path,
    format_slack,
    log_path,
    send,
    webhook_url,
)

__all__ = [
    "send",
    "SEVERITIES",
    "log_path",
    "config_path",
    "webhook_url",
    "format_slack",
]

__version__ = "0.1.0"
