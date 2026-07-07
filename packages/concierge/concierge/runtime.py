"""ClaudeCliRuntime — the built-in Runtime over `claude -p` + stream-json + --resume.

The Runtime seam is deliberately tiny (spawn / alive / observe / kill) so
flightdeck or shepherd could back it later without touching the reconciler.
Workers are started in their own session so they survive daemon restarts;
re-attach is pid + log files, both recorded in the task's attempt entry.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path

from .records import now_iso

PKG_PARENT = str(Path(__file__).resolve().parent.parent)


def spawn(home, task, prompt, cfg, resume_session=None) -> dict:
    n = len(task["attempts"]) + 1
    log_dir = home.log_dir(task["id"], n)
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        cfg.get("claude_bin", "claude"),
        "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", cfg.get("permission_mode", "bypassPermissions"),
        # workers don't inherit the parent project's MCP servers (slow init,
        # and pending server connections can hang the CLI on exit)
        *cfg.get("claude_extra_args", ["--strict-mcp-config"]),
    ]
    if resume_session:
        cmd += ["--resume", resume_session]
    env = dict(
        os.environ,
        CONCIERGE_HOME=str(home.root),
        CONCIERGE_TASK_ID=task["id"],
        PYTHONPATH=PKG_PARENT + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    with (log_dir / "agent.jsonl").open("ab") as out, (log_dir / "agent.err").open("ab") as err:
        proc = subprocess.Popen(
            cmd, cwd=home.workspace(task["id"]), stdout=out, stderr=err, env=env,
            start_new_session=True,
        )
    attempt = {
        "n": n,
        "pid": proc.pid,
        "started": now_iso(),
        "session_id": resume_session,
        "cost_usd": None,
        "result": None,
        "log": f"logs/{task['id']}/attempt-{n}",
    }
    task["attempts"].append(attempt)
    return attempt


def alive(pid) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned elsewhere (pid reuse); treat as live, wall budget will cap it


def kill(pid) -> None:
    for fn, target in ((os.killpg, pid), (os.kill, pid)):
        try:
            fn(target, signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError):
            continue


def observe(home, attempt) -> dict:
    """Update the attempt in place from its event stream (session_id, cost, outcome)."""
    p = home.root / attempt["log"] / "agent.jsonl"
    if not p.exists():
        return attempt
    for line in p.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("session_id"):
            attempt["session_id"] = ev["session_id"]
        if ev.get("type") == "result":
            attempt["cost_usd"] = ev.get("total_cost_usd")
            attempt["result"] = "error" if ev.get("is_error") else "ok"
    return attempt
