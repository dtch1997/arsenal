"""Stdio MCP server exposing the pool's control-channel tools to non-SDK
backends (the Codex backend registers it via `-c mcp_servers.concierge`).

The Claude backend gets signal_blocked / signal_waiting as in-process SDK
tools; here they are stdio MCP tools instead — but both call the exact same
file-writing logic in `concierge.signals`, so there is one implementation of
the semantics the reconciler trusts.

v1 scope cut (issue #60): Codex workers are **leaves only** — `delegate` is
deliberately NOT exposed here, only signal_blocked and signal_waiting.

Run as: `python -m concierge.mcp_stdio <home_root> <task_id> <attempt>`
(home/tid/attempt are passed as argv by the Codex wrapper; the server writes
into that CONCIERGE_HOME's mailbox and wait-sidecar).
"""
from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import signals
from .records import Home, load_config

SIGNAL_BLOCKED_DESC = (
    "Signal that you cannot proceed without human input. Post your question, "
    "then stop working; you will be resumed with the answer.")
SIGNAL_WAITING_DESC = (
    "Park this task on an external long-running job (a pod pipeline, a training "
    "run, anything computing OUTSIDE this worker) without burning a retry. "
    "Register a machine-checkable wake condition (a shell command that exits 0 "
    "when done), then stop; the daemon polls it cheaply and resumes THIS session "
    "when it fires. For tools that exit 0 even when the object is missing (e.g. "
    "`rclone lsf gcs:.../DONE`), test the OUTPUT is non-empty: "
    "`test -n \"$(rclone lsf gcs:.../DONE)\"`.")


def build_server(home: Home, tid: str, attempt: int, cfg: dict | None = None) -> FastMCP:
    """Construct the FastMCP server. Factored out (rather than inlined in main)
    so tests can inspect the registered tools without a stdio transport."""
    cfg = cfg if cfg is not None else load_config(home)
    default_timeout = cfg.get("wait_timeout_minutes", signals.WAIT_TIMEOUT_MINUTES)
    mcp = FastMCP("concierge")

    @mcp.tool(description=SIGNAL_BLOCKED_DESC)
    def signal_blocked(question: str) -> str:
        signals.post_blocked(home, tid, question)
        return "Question posted. Stop now; you will be resumed with the answer."

    @mcp.tool(description=SIGNAL_WAITING_DESC)
    def signal_waiting(until_shell: str, note: str, timeout_minutes: float | None = None) -> str:
        signals.write_waiting(
            home, tid, until_shell=until_shell, note=note, attempt=attempt,
            timeout_minutes=timeout_minutes, default_timeout=default_timeout)
        return "Wait registered. Stop now; you will be resumed when the condition fires."

    return mcp


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        raise SystemExit("usage: python -m concierge.mcp_stdio <home_root> <task_id> <attempt>")
    home = Home.locate(argv[0])
    tid, attempt = argv[1], int(argv[2])
    build_server(home, tid, attempt).run()  # stdio transport


if __name__ == "__main__":
    main()
