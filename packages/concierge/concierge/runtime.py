"""Worker — a durable handle on one agent session (one attempt of one task).

The primitive in four verbs plus a snapshot:

    Worker.spawn(home, task, prompt, cfg, resume=None) -> Worker   # start attempt N
    Worker.attach(home, task)                          -> Worker   # rebuild from the record
    worker.poll()                                      -> WorkerState
    worker.kill()

The invariant that shapes the interface: a Worker must be constructible from
the task record alone. The daemon that observes a worker is usually not the
daemon that spawned it (restarts), so identity is durable state — pid + log
dir — never an in-process handle. The agent session runs in a detached
wrapper process (concierge.worker) that self-bounds on USD and wall-clock,
so a worker stays safe even with no daemon running; the reconciler's checks
are backstops.

WorkerState joins the two sources of truth: the OS process table (`alive`)
and the event stream (`ended` — authoritative). `alive and ended` is the
`lingering` state (session over, process didn't exit — reap it).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .records import now_iso

PKG_PARENT = str(Path(__file__).resolve().parent.parent)


@dataclass(frozen=True)
class WorkerState:
    alive: bool                # OS process exists
    ended: bool                # terminal result event seen (authoritative)
    session_id: str | None
    cost_usd: float | None
    error: str | None          # result error text, if the session errored
    started: str

    @property
    def running(self) -> bool:
        return self.alive and not self.ended

    @property
    def lingering(self) -> bool:
        return self.alive and self.ended


class Worker:
    def __init__(self, home, task_id: str, n: int, pid: int, started: str):
        self.home = home
        self.task_id = task_id
        self.n = n
        self.pid = pid
        self.started = started

    def __repr__(self):
        return f"Worker({self.task_id} attempt {self.n}, pid {self.pid})"

    # -- constructors --

    @classmethod
    def spawn(cls, home, task, prompt, cfg, resume=None) -> "Worker":
        """Start a detached worker process for the task's next attempt and
        append its attempt entry to the task record (caller saves)."""
        n = len(task["attempts"]) + 1
        log_dir = home.log_dir(task["id"], n)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "prompt.md").write_text(prompt)
        cmd = [sys.executable, "-m", "concierge.worker", task["id"], str(n)]
        if resume:
            cmd += ["--resume", resume]
        env = dict(
            os.environ,
            CONCIERGE_HOME=str(home.root),
            CONCIERGE_TASK_ID=task["id"],
            PYTHONPATH=PKG_PARENT + os.pathsep + os.environ.get("PYTHONPATH", ""),
        )
        with (log_dir / "agent.err").open("ab") as err:
            proc = subprocess.Popen(cmd, cwd=home.workspace(task["id"]),
                                    stdout=err, stderr=err, env=env,
                                    start_new_session=True)
        w = cls(home, task["id"], n, proc.pid, now_iso())
        task["attempts"].append({
            "n": n, "pid": w.pid, "started": w.started, "session_id": resume,
            "cost_usd": None, "result": None, "log": f"logs/{task['id']}/attempt-{n}",
        })
        return w

    @classmethod
    def attach(cls, home, task, n: int = -1) -> "Worker | None":
        """Rebuild a handle from the task record — e.g. after a daemon restart,
        for a worker some earlier daemon spawned."""
        if not task["attempts"]:
            return None
        att = task["attempts"][n]
        return cls(home, task["id"], att["n"], att["pid"], att["started"])

    # -- verbs --

    def poll(self) -> WorkerState:
        session_id = cost = error = None
        ended = False
        p = self.home.log_dir(self.task_id, self.n) / "agent.jsonl"
        if p.exists():
            for line in p.read_text(errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("session_id"):
                    session_id = ev["session_id"]
                if ev.get("type") == "result":
                    ended = True
                    cost = ev.get("total_cost_usd")
                    error = str(ev.get("result"))[:500] if ev.get("is_error") else None
        return WorkerState(alive=self._alive(), ended=ended, session_id=session_id,
                           cost_usd=cost, error=error, started=self.started)

    def kill(self) -> None:
        for fn in (os.killpg, os.kill):  # own session → pgid == pid; fall back to the pid
            try:
                fn(self.pid, signal.SIGTERM)
                return
            except (ProcessLookupError, PermissionError):
                continue

    def sync(self, task, state: WorkerState) -> None:
        """Stamp a poll snapshot into the task's attempt entry (caller saves)."""
        att = task["attempts"][self.n - 1]
        if state.session_id:
            att["session_id"] = state.session_id
        if state.cost_usd is not None:
            att["cost_usd"] = state.cost_usd
        if state.ended:
            att["result"] = "error" if state.error else "ok"

    def _alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # pid exists under another user (reuse); backstops will cap it
        # a zombie is dead for our purposes: it exited but its parent (the
        # spawning daemon, which never wait()s) hasn't reaped it yet
        try:
            with open(f"/proc/{self.pid}/stat") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return True
