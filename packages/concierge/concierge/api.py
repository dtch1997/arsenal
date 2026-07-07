"""The public API — concierge is an asyncio-native library: import Pool and go.

Fast file-ops are plain methods; the blocking verbs (wait, wait_all, serve,
tick) are coroutines. Only two things stay shell-reachable via
`python -m concierge`: the worker's blocked-signal (`msg`) and the daemon
(`serve`).
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import reconcile, runtime
from .gates import Always, Gate
from .records import ACTIVE, TERMINAL, Home, load_config, new_id, new_task


def _spec_text(spec) -> str:
    if isinstance(spec, Path):
        return spec.read_text()
    if isinstance(spec, str) and "\n" not in spec and spec.endswith((".md", ".markdown")) and Path(spec).exists():
        return Path(spec).read_text()
    return spec


def _normalize_gate(gate) -> dict:
    if gate is None:
        return Always().to_json()
    if isinstance(gate, Gate):
        return gate.to_json()
    if isinstance(gate, dict):  # already-serialized form (e.g. via HTTP later)
        return Gate.from_json(gate).to_json()
    raise TypeError(f"gate must be a Gate (or its to_json() dict), got {type(gate).__name__}")


class Pool:
    """A handle on one CONCIERGE_HOME. Config kwargs override config.yaml:
    concurrency, daily_usd_cap, interval, permission_mode, claude_bin,
    claude_extra_args, slack_webhook, pool_cmd."""

    def __init__(self, home=None, **config):
        self.home = Home.locate(home)
        self.config = {**self._file_config(), **config}

    def _file_config(self) -> dict:
        return load_config(self.home)

    # -- requests --

    def submit(self, spec, *, title=None, repo=None, base="main", branch=None,
               access="readwrite", gate=None, budget_usd=20.0,
               budget_minutes=240.0, priority=0, max_attempts=3, notify=None) -> str:
        """Enqueue a task; returns its id. `spec` is Markdown text, or a Path
        (or existing *.md path string) to read it from. `gate` is a Gate
        object (concierge.gates), default Always()."""
        tid = new_id()
        task = new_task(
            tid,
            title=title or (Path(spec).stem if isinstance(spec, Path) else f"task {tid}"),
            gate=_normalize_gate(gate),
            budget={"usd": budget_usd, "wall_minutes": budget_minutes},
            workspace={"repo": str(repo) if repo else None, "base": base,
                       "branch": branch or f"pool/{tid}", "access": access},
            priority=priority,
            notify=notify,
            max_attempts=max_attempts,
        )
        self.home.spec_path(tid).write_text(_spec_text(spec))
        self.home.save(task)
        return tid

    def get(self, tid) -> dict:
        return self.home.load(tid)

    def tasks(self) -> list[dict]:
        return self.home.tasks()

    def msg(self, tid, text, sender="user") -> dict:
        self.home.load(tid)  # validate id
        return self.home.post(tid, sender, text)

    def messages(self, tid) -> list[dict]:
        return self.home.messages(tid)

    def cancel(self, tid) -> dict:
        task = self.home.load(tid)
        if task["status"] in TERMINAL:
            return task
        if task["status"] == "running":
            runtime.kill(task["attempts"][-1]["pid"])
        task["status"] = "cancelled"
        task["status_detail"] = "cancelled by user"
        self.home.save(task)
        return task

    def remove(self, tid) -> None:
        import shutil
        task = self.home.load(tid)
        if task["status"] in ACTIVE:
            raise RuntimeError(f"{tid} is {task['status']}; cancel it first")
        for p in (self.home.task_path(tid), self.home.spec_path(tid), self.home.mailbox_path(tid)):
            p.unlink(missing_ok=True)
        for d in (self.home.workspace(tid), self.home.root / "logs" / tid):
            shutil.rmtree(d, ignore_errors=True)

    async def wait(self, tid, *, timeout=3600.0, poll=2.0, until_blocked=False) -> dict:
        """Await the task reaching a terminal state (or blocked, if
        until_blocked); returns the final task record. Raises TimeoutError."""
        deadline = time.monotonic() + timeout
        while True:
            task = self.get(tid)
            if task["status"] in TERMINAL or (until_blocked and task["status"] == "blocked"):
                return task
            if time.monotonic() > deadline:
                raise TimeoutError(f"{tid} still {task['status']} after {timeout}s")
            await asyncio.sleep(poll)

    async def wait_all(self, tids, *, timeout=3600.0, poll=2.0) -> list[dict]:
        return list(await asyncio.gather(
            *(self.wait(t, timeout=timeout, poll=poll) for t in tids)))

    def transcript(self, tid, attempts=1) -> str:
        """Human-rendered agent event stream for the last N attempts."""
        task = self.get(tid)
        out = []
        for att in task["attempts"][-attempts:]:
            out.append(f"--- attempt {att['n']} ({att['started']}) ---")
            p = self.home.root / att["log"] / "agent.jsonl"
            if not p.exists():
                out.append("(no log yet)")
                continue
            for line in p.read_text(errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rendered = _render_event(ev)
                if rendered:
                    out.append(rendered)
        return "\n".join(out)

    # -- daemon --

    async def tick(self) -> None:
        """One reconciler pass. Runs in a thread: gate checks and git shell
        out and can block for seconds."""
        await asyncio.to_thread(reconcile.tick, self.home, self.config)

    async def serve(self, *, exit_when_idle=False, interval=None) -> None:
        interval = interval or self.config.get("interval", 3)
        print(f"[concierge] serving {self.home.root} "
              f"(concurrency={self.config.get('concurrency', 4)}, interval={interval}s)", flush=True)
        while True:
            await self.tick()
            if exit_when_idle and not any(t["status"] in ACTIVE for t in self.home.tasks()):
                print("[concierge] idle — exiting", flush=True)
                return
            await asyncio.sleep(interval)


def _render_event(ev):
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"· session {ev.get('session_id')} model={ev.get('model')}"
    if t == "assistant":
        out = []
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                arg = json.dumps(block.get("input", {}))[:120]
                out.append(f"→ {block.get('name')} {arg}")
        return "\n".join(out) or None
    if t == "result":
        status = "ERROR" if ev.get("is_error") else "ok"
        return f"■ result: {status} cost=${ev.get('total_cost_usd') or 0:.4f} turns={ev.get('num_turns')}"
    return None
