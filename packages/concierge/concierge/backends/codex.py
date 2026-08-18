"""The Codex backend: drives `codex exec --json` as the detached per-task
wrapper, normalizing the Codex event stream into the SAME agent.jsonl schema
the Claude backend emits (system/assistant/result).

Shape mirrors backends.claude: a detached process launched by
runtime.Worker.spawn as `python -m concierge.backends.codex <id> <attempt>
[--resume <thread_id>]`, self-bounded on wall-clock, exiting (with its process
group, incl. the codex child) the moment its terminal result event is flushed.

Codex mapping (smoke-tested against codex-cli 0.147.0):
  - `codex exec --json` -> JSONL event stream on stdout
  - resume: `codex exec <exec-flags> resume <thread_id> <prompt>` — exec-level
    flags MUST precede the `resume` subcommand (CLI rejects them after)
  - access readonly -> `--sandbox read-only`, readwrite -> `--sandbox workspace-write`
  - structured output -> `--output-schema <file>`; final agent_message is the JSON
  - model -> `-m` (task model, else config `codex_model`)
  - signal_blocked / signal_waiting -> stdio MCP server (concierge.mcp_stdio),
    registered via `-c mcp_servers.*` overrides scoped to this invocation
  - stdin closed (codex reads the prompt from stdin when attached)

Documented gaps vs the Claude backend (see README capability table):
  - no background-task guard hook (that is Claude-Code-specific)
  - cost is an ESTIMATE: Codex reports tokens, not USD (ChatGPT-plan auth has
    no per-token billing), so we price turn.completed usage via
    `codex_cost_per_mtoken` and stamp the estimate into the attempt.
  - codex workers are leaves only in v1 — no `delegate` tool is registered.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from ..records import Home, load_config

PKG_PARENT = str(Path(__file__).resolve().parents[2])
BACKEND = "codex"
CODEX_MODEL_DEFAULT = "gpt-5.6-sol"


# -- command construction --

def _sandbox(task: dict) -> str:
    return "read-only" if task["workspace"].get("access") == "readonly" else "workspace-write"


def _mcp_overrides(home: Home, tid: str, attempt: int) -> list[str]:
    """`-c` config overrides that register the concierge stdio MCP server for
    this invocation only — no mutation of the user's ~/.codex/config.toml.

    We use `-c` (rather than CODEX_HOME/config file) precisely so the default
    CODEX_HOME stays in effect and the ChatGPT-plan auth in ~/.codex/auth.json
    is still found. The server gets home/tid/attempt via argv (it writes the
    same mailbox + wait-sidecar files as the in-process Claude tools), and
    PYTHONPATH via env so it can import concierge even from a bare workspace.
    Values are TOML (codex parses each `-c value` as TOML)."""
    args = ["-m", "concierge.mcp_stdio", str(home.root), tid, str(attempt)]
    return [
        "-c", f'mcp_servers.concierge.command={json.dumps(sys.executable)}',
        "-c", f"mcp_servers.concierge.args={json.dumps(args)}",
        "-c", f'mcp_servers.concierge.env.PYTHONPATH={json.dumps(PKG_PARENT)}',
        "-c", f'mcp_servers.concierge.env.CONCIERGE_HOME={json.dumps(str(home.root))}',
    ]


def _strict_schema(node):
    """OpenAI's structured-output API ("strict" schemas) rejects any object
    node that doesn't set additionalProperties=false and list every property
    in `required` (400 invalid_json_schema — hit live on the first A/B task).
    Concierge submitters write plain JSON Schema, so the adapter normalizes:
    properties the submitter left optional become nullable instead."""
    if isinstance(node, list):
        return [_strict_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strict_schema(v) for k, v in node.items()}
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        out.setdefault("additionalProperties", False)
        originally_required = set(out.get("required", []))
        for name, sub in props.items():
            if name not in originally_required and isinstance(sub, dict):
                t = sub.get("type")
                if isinstance(t, str) and t != "null":
                    sub["type"] = [t, "null"]
                elif isinstance(t, list) and "null" not in t:
                    sub["type"] = [*t, "null"]
        out["required"] = list(props)
    return out


def build_command(home: Home, task: dict, cfg: dict, resume: str | None,
                  output_schema: dict | None, attempt: int, log_dir: Path) -> list[str]:
    """The full `codex exec` argv. Exec-level flags come first, then either the
    prompt (fresh) or `resume <thread_id> <prompt>` (resume) — the ordering
    gotcha the smoke test pinned."""
    codex_bin = cfg.get("codex_bin", "codex")
    model = task.get("model") or cfg.get("codex_model", CODEX_MODEL_DEFAULT)
    flags = [
        codex_bin, "exec", "--json",
        "-C", str(home.workspace(task["id"])),
        "--sandbox", _sandbox(task),
        "-m", model,
        # pool workspaces are git clones, but a bare-mkdir workspace (repo=None)
        # is not — never let codex refuse to start over the git check
        "--skip-git-repo-check",
        *_mcp_overrides(home, task["id"], attempt),
    ]
    if output_schema is not None:
        # not the Worker.spawn-written output_schema.json: codex needs the
        # OpenAI-strict normalization of it, written alongside
        schema_path = log_dir / "output_schema.codex.json"
        schema_path.write_text(json.dumps(_strict_schema(output_schema)))
        flags += ["--output-schema", str(schema_path)]
    prompt_path = log_dir / "prompt.md"
    prompt = prompt_path.read_text()
    if resume:
        # exec-level flags BEFORE the resume subcommand (0.147.0 rejects them after)
        return [*flags, "resume", resume, prompt]
    return [*flags, prompt]


# -- event-stream normalization --

def _usage_cost(usage: dict, cfg: dict) -> float:
    """Estimate USD from Codex token usage. `codex_cost_per_mtoken` is either a
    scalar (applied to input and output) or {"input": x, "output": y} in $/Mtoken.
    Cached input is billed at the input rate too (a conservative overestimate);
    without the config key, cost is 0 (ChatGPT-plan auth is not per-token billed)."""
    rates = cfg.get("codex_cost_per_mtoken") or {}
    if isinstance(rates, (int, float)):
        r_in = r_out = float(rates)
    else:
        r_in = float(rates.get("input", 0) or 0)
        r_out = float(rates.get("output", 0) or 0)
    tin = usage.get("input_tokens", 0) or 0
    tout = usage.get("output_tokens", 0) or 0
    return (tin * r_in + tout * r_out) / 1_000_000


class _Stream:
    """Accumulates the running state a Codex event stream implies, and turns
    each event into zero or one agent.jsonl event. The terminal result event is
    synthesized from accumulated state when the stream closes."""

    def __init__(self, cfg: dict, output_schema: dict | None):
        self.cfg = cfg
        self.output_schema = output_schema
        self.thread_id: str | None = None
        self.last_text: str | None = None
        self.usage: dict = {}
        self.turns = 0
        self.error: str | None = None

    def feed(self, ev: dict) -> dict | None:
        t = ev.get("type")
        if t == "thread.started":
            self.thread_id = ev.get("thread_id")
            return {"type": "system", "subtype": "init", "backend": BACKEND,
                    "session_id": self.thread_id, "model": ev.get("model")}
        if t == "turn.completed":
            self.turns += 1
            if isinstance(ev.get("usage"), dict):
                self.usage = ev["usage"]
            return None
        if t in ("turn.failed", "error"):
            self.error = _event_error_text(ev)
            return None
        if t == "item.completed":
            return self._item(ev.get("item") or {})
        return None  # turn.started, item.started, reasoning deltas, etc.

    def _item(self, item: dict) -> dict | None:
        itype = item.get("type") or item.get("item_type")
        if itype == "agent_message":
            text = item.get("text") or ""
            if not text.strip():
                return None
            self.last_text = text
            return {"type": "assistant", "session_id": self.thread_id,
                    "message": {"content": [{"type": "text", "text": text}]}}
        if itype == "command_execution":
            # surface shell work as a tool_use block so transcripts read like
            # the Claude ones; input mirrors what the model ran
            return {"type": "assistant", "session_id": self.thread_id,
                    "message": {"content": [{
                        "type": "tool_use", "id": item.get("id"), "name": "shell",
                        "input": {"command": item.get("command"),
                                  "exit_code": item.get("exit_code")}}]}}
        if itype == "error":
            self.error = item.get("message") or item.get("text") or "codex item error"
            return None
        return None  # reasoning, todo_list, web_search, mcp_tool_call, file_change...

    def result_event(self, is_error: bool, extra_result: str | None = None) -> dict:
        err = is_error or self.error is not None
        structured = None
        if self.output_schema is not None and self.last_text and not err:
            try:
                structured = json.loads(self.last_text)
            except (json.JSONDecodeError, TypeError):
                structured = None
        result_text = extra_result or self.error or self.last_text
        return {"type": "result",
                "subtype": "error" if err else "success",
                "is_error": err, "num_turns": self.turns,
                "total_cost_usd": _usage_cost(self.usage, self.cfg),
                "session_id": self.thread_id, "result": result_text,
                "structured_output": structured}


def _event_error_text(ev: dict) -> str:
    return (ev.get("error") or ev.get("message")
            or (ev.get("turn") or {}).get("error") or "codex turn failed")


# -- the wrapper run loop --

async def run(home: Home, task: dict, out, resume: str | None,
              output_schema: dict | None, attempt: int) -> int:
    cfg = load_config(home)
    log_dir = home.log_dir(task["id"], attempt)
    cmd = build_command(home, task, cfg, resume, output_schema, attempt, log_dir)
    stream = _Stream(cfg, output_schema)

    def emit(ev):
        out.write(json.dumps(ev, default=str) + "\n")
        out.flush()

    def die():
        out.flush()
        # take down our process group — the codex child runs in it (we were
        # spawned start_new_session=True), so this reaps it deterministically
        os.killpg(os.getpgid(0), signal.SIGTERM)
        os._exit(0)

    async def consume():
        # env passes PYTHONPATH so the codex-spawned MCP server subprocess can
        # import concierge; stdin closed so codex doesn't block reading it
        env = dict(os.environ)
        env["PYTHONPATH"] = PKG_PARENT + os.pathsep + os.environ.get("PYTHONPATH", "")
        err_f = (log_dir / "codex.err").open("ab")
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(home.workspace(task["id"])),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=err_f, env=env)
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                norm = stream.feed(ev)
                if norm:
                    emit(norm)
        finally:
            err_f.close()
        rc = await proc.wait()
        emit(stream.result_event(is_error=(rc != 0)))
        die()

    wall_minutes = task["budget"]["wall_minutes"]
    try:
        await asyncio.wait_for(consume(), timeout=wall_minutes * 60)
        return 0
    except asyncio.TimeoutError:
        emit({"type": "result", "subtype": "wall_timeout", "is_error": True,
              "total_cost_usd": _usage_cost(stream.usage, cfg),
              "session_id": stream.thread_id,
              "result": f"worker self-terminated: wall budget ({wall_minutes}m) exceeded"})
        die()
        return 2
    except Exception as e:  # surface as a terminal result so the reconciler settles
        emit({"type": "result", "subtype": "codex_error", "is_error": True,
              "total_cost_usd": _usage_cost(stream.usage, cfg),
              "session_id": stream.thread_id, "result": f"{type(e).__name__}: {e}"})
        print(f"[concierge.codex] {type(e).__name__}: {e}", file=sys.stderr)
        die()
        return 1


def main():
    ap = argparse.ArgumentParser(prog="python -m concierge.backends.codex")
    ap.add_argument("task_id")
    ap.add_argument("attempt", type=int)
    ap.add_argument("--resume", default=None)
    args = ap.parse_args()

    home = Home.locate(os.environ.get("CONCIERGE_HOME"))
    task = home.load(args.task_id)
    log_dir = home.log_dir(args.task_id, args.attempt)
    schema_path = log_dir / "output_schema.json"
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else None
    # house rules -> workspace AGENTS.md (kept out of PRs), the Codex analogue
    # of the Claude backend's system-prompt append
    from .. import provision
    provision.install_house_rules(home.workspace(args.task_id), home.root)
    with (log_dir / "agent.jsonl").open("a") as out:
        raise SystemExit(asyncio.run(run(home, task, out, args.resume, schema, args.attempt)))


if __name__ == "__main__":
    main()
