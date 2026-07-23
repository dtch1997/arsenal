"""The stateless reconciler: each tick, observe tasks/*.json + processes + logs, then act.

All decisions follow the observation→action table in SPEC.md. The daemon can
die and restart at any point; workers run in their own sessions and are
re-attached via pid + log paths recorded in the task record.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime

from . import gates, provision, runtime
from .notify import notify
from .records import now_iso


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
with the answer. If your deliverable depends on an external long-running job
(pod pipeline, training run), do not wait in-session for more than a few
minutes and never ship placeholder results: call the `signal_waiting` tool
with a cheap shell probe that exits 0 when the job is done, then stop; you
will be resumed to finish when it fires. If the task splits into independent,
parallelizable subtasks (each with an externally checkable definition of
done), you may call up new workers within the pool via the `delegate` tool —
one call per child, then park on the probe it returns via `signal_waiting`;
never wait in-session for children. A sequential pipeline is one task
orchestrated with stagehand, not a chain of children. Otherwise, complete the
task so the gate passes, then stop.

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
        provision.install_guard_hook(ws)
        return
    subprocess.run(["git", "clone", "--quiet", w["repo"], str(ws)], check=True)
    r = subprocess.run(["git", "-C", str(ws), "checkout", "-q", "-b", w["branch"], w["base"]],
                       capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["git", "-C", str(ws), "checkout", "-q", "-b", w["branch"],
                        f"origin/{w['base']}"], check=True)
    provision.install_guard_hook(ws)


def _dispatch(home, cfg, task):
    ws = home.workspace(task["id"])
    if not ws.exists():
        _make_workspace(task, ws)
    # a stale wait sidecar from a prior attempt must never be honored against a
    # fresh attempt 1 (attempt-number match is the guard, but clean up anyway)
    home.wait_path(task["id"]).unlink(missing_ok=True)
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

    # the worker may have parked on an external job via signal_waiting: honor a
    # sidecar written by the CURRENT attempt BEFORE the gate check, so waiting
    # burns no attempt and writes no gate_result. Stale sidecars (from an older
    # attempt) are deleted, not honored.
    wait_p = home.wait_path(task["id"])
    if wait_p.exists():
        try:
            sidecar = json.loads(wait_p.read_text())
        except (OSError, ValueError):
            sidecar = None
        wait_p.unlink(missing_ok=True)
        latest = task["attempts"][-1]["n"] if task["attempts"] else 0
        if sidecar and sidecar.get("attempt") == latest:
            task["wait"] = {**sidecar, "since": now_iso(), "last_polled": None}
            task["status"] = "waiting"
            task["status_detail"] = f"waiting: {sidecar['note']}"
            home.save(task)
            notify(cfg, task, "waiting", sidecar["note"])
            return

    # worker exited → the pool decides, never the worker
    verdict = gates.check(task, home.workspace(task["id"]))
    # persist the evaluated verdict as structured data (not just prose in
    # status_detail) — set once here so every downstream save path carries it
    task["gate_result"] = {"passed": bool(verdict), "detail": verdict.detail,
                           "checked_at": now_iso()}
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

    # a gate-checked failure is what burns a strike — NOT a resume from
    # blocked/waiting, which leave attempts as pure history. Count gate
    # failures on their own counter (backwards compat: default 0).
    task["gate_failures"] = task.get("gate_failures", 0) + 1
    total_cost = sum(a.get("cost_usd") or 0 for a in task["attempts"])
    if total_cost >= task["budget"]["usd"]:
        _finish(home, cfg, task, "failed", f"usd budget exceeded (${total_cost:.2f} >= ${task['budget']['usd']})")
    elif task["gate_failures"] >= task["max_attempts"]:
        _finish(home, cfg, task, "failed",
                f"gate failed after {task['gate_failures']} gate-checked attempts — {verdict.detail}")
    else:
        _resume(home, cfg, task,
                f"Your completion gate failed — {verdict.detail}. Fix this so the gate passes, "
                "then stop. If the gate is failing because results are still computing remotely "
                "(a pod pipeline, training run, or other external long-running job), do NOT ship "
                "placeholder results to satisfy it and do NOT wait in-session — call the "
                "`signal_waiting` tool with a cheap shell probe that exits 0 once the job is done, "
                "then stop; you will be resumed to finish when it fires.")


def _maybe_unblock(home, cfg, task):
    msgs = home.messages(task["id"])
    if any(m["from"] == "user" for m in msgs[task["mail_delivered"]:]):
        _resume(home, cfg, task, "The user replied to your question.")


def _archive_wait(task):
    """Move the live wait onto wait_history and clear it (caller resumes/saves)."""
    wait = task.pop("wait", None)
    if wait is not None:
        task.setdefault("wait_history", []).append({**wait, "resolved_at": now_iso()})


def _maybe_wake(home, cfg, task):
    """Poll a waiting task's wake condition; resume the same session when it
    fires, times out, or the user speaks. Waiting burns no attempt."""
    wait = task["wait"]
    note = wait["note"]

    # a user message wakes a waiting task immediately, same as _maybe_unblock —
    # regardless of the probe
    msgs = home.messages(task["id"])
    if any(m["from"] == "user" for m in msgs[task["mail_delivered"]:]):
        _archive_wait(task)
        _resume(home, cfg, task, "The user replied while you were waiting.")
        return

    # give up after timeout_minutes — a normal attempt resumes to finish honestly
    if _minutes_since(wait["since"]) > wait["timeout_minutes"]:
        _archive_wait(task)
        _resume(home, cfg, task,
                f"Your wait timed out after {wait['timeout_minutes']:.0f} minutes "
                f"(waiting on: {note}). Investigate the external job — if it failed, "
                "say so honestly in your report and finish however the gate allows; "
                "do not restart a multi-hour pipeline without checking budget.")
        return

    # poll throttle: only run the probe every wait_poll_seconds
    poll_seconds = cfg.get("wait_poll_seconds", 60)
    last = wait.get("last_polled")
    if last is not None and (datetime.now() - datetime.strptime(last, "%Y-%m-%dT%H:%M:%S")).total_seconds() < poll_seconds:
        return
    wait["last_polled"] = now_iso()
    home.save(task)

    try:
        r = subprocess.run(wait["until_shell"], shell=True, cwd=home.workspace(task["id"]),
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return  # probe hung this round; try again next poll
    if r.returncode == 0:
        tail = (r.stdout + r.stderr).strip()[-500:]
        _archive_wait(task)
        _resume(home, cfg, task,
                f"Your wait condition fired ({note}): the probe succeeded"
                f"{f' — {tail}' if tail else ''}. Finish the task now so the gate "
                "passes, then stop.")


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
        elif task["status"] == "waiting":
            _maybe_wake(home, cfg, task)

    # waiting tasks are parked on external work — they hold no worker slot
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
