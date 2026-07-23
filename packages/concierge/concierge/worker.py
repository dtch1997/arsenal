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
import signal
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

from . import delegation
from .records import Home, load_config, now_iso

READONLY_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
BLOCKED_TOOL = "mcp__concierge__signal_blocked"
WAITING_TOOL = "mcp__concierge__signal_waiting"
DELEGATE_TOOL = "mcp__concierge__delegate"
WAIT_TIMEOUT_MINUTES = 720


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
                "session_id": message.session_id, "result": message.result,
                "structured_output": message.structured_output}
    return None


def _options(home: Home, task: dict, cfg: dict, resume: str | None,
             output_schema: dict | None, attempt: int) -> ClaudeAgentOptions:
    mailbox_server = create_sdk_mcp_server(
        name="concierge",
        tools=[_blocked_tool(home, task["id"]),
               _waiting_tool(home, task["id"], cfg, attempt),
               _delegate_tool(home, task["id"], cfg)])
    readonly = task["workspace"].get("access") == "readonly"
    spent = sum(a.get("cost_usd") or 0 for a in task["attempts"])
    remaining = max(0.5, task["budget"]["usd"] - spent)
    # HOUSE_RULES.md: pool-level conventions injected into every worker's
    # system prompt — the harness soul (artifact paths, tooling norms, report
    # standards) that a bare workspace clone wouldn't otherwise carry
    rules = home.root / "HOUSE_RULES.md"
    system_prompt = ({"type": "preset", "preset": "claude_code",
                      "append": "# Pool house rules\n\n" + rules.read_text()}
                     if rules.exists() else None)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        output_format={"type": "json_schema", "schema": output_schema} if output_schema else None,
        cwd=str(home.workspace(task["id"])),
        resume=resume,
        mcp_servers={"concierge": mailbox_server},
        # delegate is withheld from readonly workers: a readonly parent minting
        # readwrite children would be a privilege escalation
        allowed_tools=(READONLY_TOOLS if readonly else [DELEGATE_TOOL])
                      + [BLOCKED_TOOL, WAITING_TOOL],
        # per-task model override — the trees-and-leaves economics knob: parents
        # run the default (frontier) model, leaves can be delegated down-tier
        model=task.get("model"),
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


def _waiting_tool(home: Home, tid: str, cfg: dict, attempt: int):
    default_timeout = cfg.get("wait_timeout_minutes", WAIT_TIMEOUT_MINUTES)

    @tool("signal_waiting",
          "Park this task on an external long-running job (a pod pipeline, a "
          "training run, anything computing OUTSIDE this worker) without burning "
          "a retry. Register a machine-checkable wake condition, then stop; the "
          "daemon polls it cheaply and resumes THIS session when it fires, so "
          "you can finish the report/PR. Make the probe self-contained and cheap "
          "(it runs repeatedly in your workspace). For tools that exit 0 even "
          "when the object is missing (e.g. `rclone lsf gcs:.../DONE`), test the "
          "OUTPUT is non-empty, not the exit code: "
          "`test -n \"$(rclone lsf gcs:.../DONE)\"`.",
          # explicit schema so timeout_minutes is genuinely optional (the
          # {name: type} shorthand marks every field required)
          {"type": "object",
           "properties": {
               "until_shell": {"type": "string",
                               "description": "shell command run in the workspace; exit 0 = condition met"},
               "note": {"type": "string",
                        "description": "human-readable description of what is being waited on"},
               "timeout_minutes": {"type": "number",
                                   "description": "give up waiting after this long (default from config, 720)"},
           },
           "required": ["until_shell", "note"]})
    async def signal_waiting(args):
        timeout = args.get("timeout_minutes")
        sidecar = {
            "until_shell": str(args["until_shell"]),
            "note": str(args["note"]),
            "timeout_minutes": float(timeout) if timeout is not None else default_timeout,
            "requested_at": now_iso(),
            "attempt": attempt,
        }
        # atomic write (temp + rename), same pattern as Home.save — the daemon
        # owns the task record, so we only ever touch this sidecar
        p = home.wait_path(tid)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sidecar, indent=2))
        os.replace(tmp, p)
        return {"content": [{"type": "text",
                             "text": "Wait registered. Stop now; you will be "
                                     "resumed when the condition fires."}]}
    return signal_waiting


def _delegate_tool(home: Home, tid: str, cfg: dict):
    @tool("delegate",
          "Call up a new worker WITHIN your pool for one independent subtask "
          "(trees and leaves). The child is an ordinary pool task: it queues "
          "behind the pool's concurrency cap and dispatches when a slot frees. "
          "Delegate only subtasks that are independent and parallelizable, each "
          "with an externally checkable definition of done — a sequential "
          "pipeline is ONE task orchestrated with stagehand, not a chain of "
          "children. Write the child's spec the way you'd want to receive it: "
          "all ambiguity collapsed, inputs/outputs explicit. Children building "
          "on your work: commit AND push your branch first, then pass "
          "base=<your branch>. After delegating ALL children, call "
          "signal_waiting with the probe this tool returns, then stop; you will "
          "be resumed with every child's outcome (including failures — you are "
          "the recovery mechanism: retry with a sharper spec, absorb the work, "
          "or signal_blocked). Never wait in-session for children.",
          {"type": "object",
           "properties": {
               "title": {"type": "string", "description": "short child task title"},
               "spec": {"type": "string",
                        "description": "full Markdown spec for the child — self-contained; "
                                       "the child sees nothing of your session"},
               "gate": {"type": "object",
                        "description": "serialized completion gate, checked externally after "
                                       "the child exits (default: none). Examples: "
                                       '{"kind": "pr_open"}, '
                                       '{"kind": "shell_ok", "cmd": "test -s results.jsonl"}, '
                                       '{"kind": "all_of", "gates": [...]}'},
               "budget_usd": {"type": "number",
                              "description": "child USD budget, carved from your remaining "
                                             "budget (default 10 or what remains)"},
               "budget_minutes": {"type": "number",
                                  "description": "child wall-clock budget (default 120)"},
               "model": {"type": "string",
                         "description": "model for the child worker (default: inherit yours). "
                                        "Use a cheaper model for mechanical, fully-specified leaves"},
               "base": {"type": "string",
                        "description": "git base for the child's branch (default: your task's "
                                       "base). Pass your own PUSHED branch for children that "
                                       "build on your work"},
           },
           "required": ["title", "spec"]})
    async def delegate(args):
        # read-only load — the daemon owns this record; we only ever create new
        # child records, never write our own
        parent = home.load(tid)
        try:
            child = delegation.delegate_child(
                home, parent, cfg,
                title=str(args["title"]), spec=str(args["spec"]),
                gate=args.get("gate"), budget_usd=args.get("budget_usd"),
                budget_minutes=args.get("budget_minutes"),
                model=args.get("model"), base=args.get("base"))
        except delegation.DelegationError as e:
            return {"content": [{"type": "text", "text": f"Delegation refused: {e}"}]}
        probe = f"{sys.executable} -m concierge --home {home.root} probe-children {tid}"
        return {"content": [{"type": "text", "text":
                f"Delegated {child['id']} ({child['title']}) at depth {child['depth']}, "
                f"budget ${child['budget']['usd']:.2f}, model "
                f"{child['model'] or 'default'}. It dispatches when a pool slot frees. "
                f"When you have delegated all children, call signal_waiting with "
                f"until_shell = `{probe}` and stop; you will be resumed with each "
                f"child's outcome."}]}
    return delegate


async def run(home: Home, task: dict, prompt: str, out, resume: str | None,
              output_schema: dict | None = None, attempt: int = 1) -> int:
    def emit(ev):
        out.write(json.dumps(ev, default=str) + "\n")
        out.flush()

    def die():
        # the terminal result event is flushed; exit deterministically by
        # taking down our own process group (incl. the SDK's CLI child) —
        # transport/child cleanup can hang after the session ends, and a
        # lingering wrapper is an orphan when no daemon is running to reap it
        out.flush()
        os.killpg(os.getpgid(0), signal.SIGTERM)
        os._exit(0)  # unreachable unless SIGTERM is masked

    async def consume():
        async for message in query(prompt=prompt,
                                   options=_options(home, task, load_config(home), resume, output_schema, attempt)):
            ev = _normalize(message)
            if ev:
                emit(ev)
            if ev and ev["type"] == "result":
                die()

    # workers self-bound on wall-clock so they stay safe with no daemon
    # running; the reconciler's wall check is only a backstop
    wall_minutes = task["budget"]["wall_minutes"]
    try:
        await asyncio.wait_for(consume(), timeout=wall_minutes * 60)
        return 0
    except asyncio.TimeoutError:
        emit({"type": "result", "subtype": "wall_timeout", "is_error": True,
              "total_cost_usd": None,
              "result": f"worker self-terminated: wall budget ({wall_minutes}m) exceeded"})
        die()
        return 2
    except Exception as e:  # surface as a terminal result event so the reconciler settles the attempt
        emit({"type": "result", "subtype": "sdk_error", "is_error": True,
              "total_cost_usd": None, "result": f"{type(e).__name__}: {e}"})
        print(f"[concierge.worker] {type(e).__name__}: {e}", file=sys.stderr)
        die()
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
    schema_path = log_dir / "output_schema.json"  # written by Worker.spawn when the attempt has one
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else None
    with (log_dir / "agent.jsonl").open("a") as out:
        raise SystemExit(asyncio.run(run(home, task, prompt, out, args.resume, schema, args.attempt)))


if __name__ == "__main__":
    main()
