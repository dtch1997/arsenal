"""The stateless reconciler: each tick, observe tasks/*.json + processes + logs, then act.

All decisions follow the observation→action table in SPEC.md. The daemon can
die and restart at any point; workers run in their own sessions and are
re-attached via pid + log paths recorded in the task record.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime

from . import gates, runtime
from .notify import notify


def _minutes_since(ts: str) -> float:
    then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    return (datetime.now() - then).total_seconds() / 60


def _preamble(task) -> str:
    gate_desc = gates.Gate.from_json(task["gate"]).describe()
    return f"""You are the worker for pool task {task['id']}: {task['title']}.
Work in the current directory (your dedicated workspace).
Your completion gate, checked externally after you exit: {gate_desc}.
If and only if you cannot proceed without human input, call the
`signal_blocked` tool with your question, then stop; you will be resumed
with the answer. Otherwise, complete the task so the gate passes, then stop.

--- TASK SPEC ---
"""


def _last_session(task):
    return next((a["session_id"] for a in reversed(task["attempts"]) if a.get("session_id")), None)


def _finish(home, cfg, task, status, detail):
    task["status"] = status
    task["status_detail"] = detail
    home.save(task)
    notify(cfg, task, status, detail)


def _resume(home, cfg, task, text):
    msgs = home.messages(task["id"])
    pending_user = [m for m in msgs[task["mail_delivered"]:] if m["from"] == "user"]
    if pending_user:
        text += "\n\nMessages from the user:\n" + "\n".join(f"- {m['text']}" for m in pending_user)
    task["mail_delivered"] = len(msgs)
    sid = _last_session(task)
    if sid is None:
        # previous attempt died before a session existed — start cold with the full prompt
        spec = home.spec_path(task["id"]).read_text()
        text = _preamble(task) + spec + f"\n\n(Note: {text})"
    runtime.Worker.spawn(home, task, text, cfg, resume=sid)
    task["status"] = "running"
    task["status_detail"] = ""
    home.save(task)
    print(f"[concierge] {task['id']} resumed (attempt {len(task['attempts'])})", flush=True)


def _make_workspace(task, ws):
    w = task["workspace"]
    if not w.get("repo"):
        ws.mkdir(parents=True)
        return
    subprocess.run(["git", "clone", "--quiet", w["repo"], str(ws)], check=True)
    r = subprocess.run(["git", "-C", str(ws), "checkout", "-q", "-b", w["branch"], w["base"]],
                       capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["git", "-C", str(ws), "checkout", "-q", "-b", w["branch"],
                        f"origin/{w['base']}"], check=True)


def _dispatch(home, cfg, task):
    ws = home.workspace(task["id"])
    if not ws.exists():
        _make_workspace(task, ws)
    spec = home.spec_path(task["id"]).read_text()
    prompt = _preamble(task) + spec
    task["mail_delivered"] = len(home.messages(task["id"]))
    runtime.Worker.spawn(home, task, prompt, cfg)
    task["status"] = "running"
    home.save(task)
    print(f"[concierge] {task['id']} dispatched", flush=True)


def _refresh_running(home, cfg, task):
    worker = runtime.Worker.attach(home, task)
    if worker is None:
        return
    state = worker.poll()
    worker.sync(task, state)
    if state.running:
        # backstop only — the worker self-bounds on wall-clock in-process
        mins = _minutes_since(worker.started)
        if mins > task["budget"]["wall_minutes"] + 2:
            worker.kill()
            _finish(home, cfg, task, "failed",
                    f"wall budget exceeded ({mins:.0f}m > {task['budget']['wall_minutes']}m)")
        else:
            home.save(task)  # persist freshly observed session_id/cost
        return
    if state.lingering:
        worker.kill()  # session over (event stream is authoritative), process didn't exit

    # worker exited → the pool decides, never the worker
    verdict = gates.check(task, home.workspace(task["id"]))
    if verdict:
        task["links"].update({k: v for k, v in verdict.links.items() if v})
        task["output"] = state.output
        task["result_text"] = state.text
        _finish(home, cfg, task, "done", verdict.detail)
        return

    msgs = home.messages(task["id"])
    last_worker = max((i for i, m in enumerate(msgs) if m["from"] == "worker"), default=-1)
    if last_worker >= 0 and not any(m["from"] == "user" for m in msgs[last_worker + 1:]):
        question = msgs[last_worker]["text"]
        task["status"] = "blocked"
        task["status_detail"] = question
        home.save(task)
        notify(cfg, task, "blocked", question)
        return

    total_cost = sum(a.get("cost_usd") or 0 for a in task["attempts"])
    if total_cost >= task["budget"]["usd"]:
        _finish(home, cfg, task, "failed", f"usd budget exceeded (${total_cost:.2f} >= ${task['budget']['usd']})")
    elif len(task["attempts"]) >= task["max_attempts"]:
        _finish(home, cfg, task, "failed",
                f"gate failed after {len(task['attempts'])} attempts — {verdict.detail}")
    else:
        _resume(home, cfg, task,
                f"Your completion gate failed — {verdict.detail}. Fix this so the gate passes, then stop.")


def _maybe_unblock(home, cfg, task):
    msgs = home.messages(task["id"])
    if any(m["from"] == "user" for m in msgs[task["mail_delivered"]:]):
        _resume(home, cfg, task, "The user replied to your question.")


def _spent_today(tasks) -> float:
    today = time.strftime("%Y-%m-%d")
    return sum(a.get("cost_usd") or 0
               for t in tasks for a in t["attempts"]
               if a["started"].startswith(today))


def tick(home, cfg):
    tasks = home.tasks()
    for task in tasks:
        if task["status"] == "running":
            _refresh_running(home, cfg, task)
        elif task["status"] == "blocked":
            _maybe_unblock(home, cfg, task)

    active = sum(1 for t in tasks if t["status"] == "running")
    cap = cfg.get("daily_usd_cap", 50)
    queued = sorted((t for t in tasks if t["status"] == "queued"),
                    key=lambda t: (-t["priority"], t["created"]))
    for task in queued:
        if active >= cfg.get("concurrency", 4):
            break
        if _spent_today(tasks) >= cap:
            print(f"[concierge] daily cap ${cap} reached; holding {len(queued)} queued task(s)", flush=True)
            break
        _dispatch(home, cfg, task)
        active += 1
