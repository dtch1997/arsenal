"""The per-task worker process: runs an Agent SDK session, detached from the daemon.

The daemon must never host agent sessions in-process (daemon death would kill
workers — the flaw that ruled out flightdeck), so runtime.spawn launches this
module as its own detached process. It runs the SDK session, normalizes the
typed message stream into logs/<id>/attempt-N/agent.jsonl (the same schema
observe()/transcript() already read), and exits when the session does.

The blocked-signal is an in-process custom tool here (signal_blocked), not a
shell shim: the worker calls a tool, we append to the task mailbox directly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from .records import Home, load_config

READONLY_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
BLOCKED_TOOL = "mcp__concierge__signal_blocked"


def _normalize(message) -> dict | None:
    if isinstance(message, SystemMessage) and message.subtype == "init":
        return {"type": "system", "subtype": "init", **message.data}
    if isinstance(message, AssistantMessage):
        blocks = []
        for b in message.content:
            if isinstance(b, TextBlock) and b.text.strip():
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        if not blocks:
            return None
        return {"type": "assistant", "session_id": message.session_id,
                "message": {"content": blocks}}
    if isinstance(message, ResultMessage):
        return {"type": "result", "subtype": message.subtype,
                "is_error": message.is_error, "num_turns": message.num_turns,
                "total_cost_usd": message.total_cost_usd,
                "session_id": message.session_id, "result": message.result}
    return None


def _options(home: Home, task: dict, cfg: dict, resume: str | None) -> ClaudeAgentOptions:
    mailbox_server = create_sdk_mcp_server(name="concierge", tools=[_blocked_tool(home, task["id"])])
    readonly = task["workspace"].get("access") == "readonly"
    spent = sum(a.get("cost_usd") or 0 for a in task["attempts"])
    remaining = max(0.5, task["budget"]["usd"] - spent)
    return ClaudeAgentOptions(
        cwd=str(home.workspace(task["id"])),
        resume=resume,
        mcp_servers={"concierge": mailbox_server},
        allowed_tools=(READONLY_TOOLS if readonly else []) + [BLOCKED_TOOL],
        # readonly: nothing outside the allowlist gets auto-approved, and there
        # is no human to approve — write tools are effectively denied
        permission_mode=None if readonly else cfg.get("permission_mode", "bypassPermissions"),
        # the workspace repo's own .claude/CLAUDE.md is context the worker
        # should see; the spawning user's global config (MCP servers etc.) is not
        setting_sources=cfg.get("setting_sources", ["project"]),
        max_budget_usd=remaining,  # belt-and-braces; the pool enforces the real budget
    )


def _blocked_tool(home: Home, tid: str):
    @tool("signal_blocked",
          "Signal that you cannot proceed without human input. Post your question, "
          "then stop working; you will be resumed with the answer.",
          {"question": str})
    async def signal_blocked(args):
        home.post(tid, "worker", str(args["question"]), via="tool")
        return {"content": [{"type": "text",
                             "text": "Question posted. Stop now; you will be resumed with the answer."}]}
    return signal_blocked


async def run(home: Home, task: dict, prompt: str, out, resume: str | None) -> int:
    def emit(ev):
        out.write(json.dumps(ev, default=str) + "\n")
        out.flush()

    try:
        async for message in query(prompt=prompt, options=_options(home, task, load_config(home), resume)):
            ev = _normalize(message)
            if ev:
                emit(ev)
        return 0
    except Exception as e:  # surface as a terminal result event so the reconciler settles the attempt
        emit({"type": "result", "subtype": "sdk_error", "is_error": True,
              "total_cost_usd": None, "result": f"{type(e).__name__}: {e}"})
        print(f"[concierge.worker] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def main():
    ap = argparse.ArgumentParser(prog="python -m concierge.worker")
    ap.add_argument("task_id")
    ap.add_argument("attempt", type=int)
    ap.add_argument("--resume", default=None)
    args = ap.parse_args()

    home = Home.locate(os.environ.get("CONCIERGE_HOME"))
    task = home.load(args.task_id)
    log_dir = home.log_dir(args.task_id, args.attempt)
    prompt = (log_dir / "prompt.md").read_text()
    with (log_dir / "agent.jsonl").open("a") as out:
        raise SystemExit(asyncio.run(run(home, task, prompt, out, args.resume)))


if __name__ == "__main__":
    main()
