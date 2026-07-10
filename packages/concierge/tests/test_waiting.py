"""The `waiting` task state (issue #2): a worker parks on an external pipeline
via signal_waiting, the daemon polls the wake condition, and no attempt is
burned while waiting. Also pins the gate_failures accounting split."""
import json
import subprocess
from datetime import datetime, timedelta

import pytest

from concierge import api, gates, reconcile, runtime
from concierge.gates import Verdict
from concierge.records import ACTIVE, Home, new_task, now_iso
from concierge.runtime import WorkerState


def _home_task(tmp_path, status="running"):
    home = Home(tmp_path / "home")
    task = new_task("t-x", "t", {"kind": "always"}, {"usd": 10, "wall_minutes": 60},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    task["status"] = status
    task["attempts"].append({"n": 1, "pid": 1, "started": now_iso(),
                             "session_id": "sess-1", "cost_usd": 0.5,
                             "result": None, "log": "logs/t-x/attempt-1"})
    home.save(task)
    home.workspace("t-x").mkdir(parents=True, exist_ok=True)
    return home, task


class _FakeWorker:
    def __init__(self):
        self.started = now_iso()

    def poll(self):
        return WorkerState(alive=False, ended=True, session_id="sess-1", cost_usd=0.5,
                           error=None, started=self.started, text="done", output=None)

    def sync(self, task, state):
        pass

    def kill(self):
        pass


def _write_wait(home, tid, attempt=1, until_shell="true", note="pod pipeline",
                timeout_minutes=720):
    home.wait_path(tid).write_text(json.dumps({
        "until_shell": until_shell, "note": note,
        "timeout_minutes": timeout_minutes, "requested_at": now_iso(),
        "attempt": attempt,
    }))


def _capture_resume(monkeypatch):
    """Patch Worker.spawn so resumes are observable and record nothing runs."""
    seen = {}

    def fake_spawn(cls, home, task, text, cfg, resume=None, output_schema=None):
        seen["text"] = text
        seen["resume"] = resume
        task["attempts"].append({"n": len(task["attempts"]) + 1, "pid": 2,
                                 "started": now_iso(), "session_id": None,
                                 "cost_usd": None, "result": None, "log": "x"})
        return _FakeWorker()

    monkeypatch.setattr(runtime.Worker, "spawn", classmethod(fake_spawn))
    return seen


# -- the signal_waiting worker tool --

def test_signal_waiting_tool_writes_current_attempt_sidecar(tmp_path):
    import asyncio
    from concierge import worker
    home = Home(tmp_path / "home")
    tool = worker._waiting_tool(home, "t-x", {}, attempt=2)
    asyncio.run(tool.handler({"until_shell": "true", "note": "pod done"}))
    sc = json.loads(home.wait_path("t-x").read_text())
    assert sc["attempt"] == 2                    # stamped with THIS attempt
    assert sc["timeout_minutes"] == 720          # config default when omitted
    assert sc["note"] == "pod done" and sc["until_shell"] == "true"
    assert sc["requested_at"]
    assert not list((home.root / "tasks").glob("*.tmp"))  # atomic write, no temp left


def test_signal_waiting_tool_honors_config_and_explicit_timeout(tmp_path):
    import asyncio
    from concierge import worker
    home = Home(tmp_path / "home")
    tool = worker._waiting_tool(home, "t-x", {"wait_timeout_minutes": 90}, attempt=1)
    asyncio.run(tool.handler({"until_shell": "true", "note": "n"}))
    assert json.loads(home.wait_path("t-x").read_text())["timeout_minutes"] == 90
    asyncio.run(tool.handler({"until_shell": "true", "note": "n", "timeout_minutes": 15}))
    assert json.loads(home.wait_path("t-x").read_text())["timeout_minutes"] == 15


# -- sidecar consumption --

def test_signal_waiting_sidecar_parks_task(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    _write_wait(home, "t-x", attempt=1, note="stage-3 checkpoint")
    monkeypatch.setattr(runtime.Worker, "attach", classmethod(lambda cls, h, t: _FakeWorker()))

    checked = []
    monkeypatch.setattr(gates, "check", lambda t, ws: checked.append(1) or Verdict(True, "x"))
    notes = []
    monkeypatch.setattr(reconcile, "notify",
                        lambda cfg, t, status, detail: notes.append((status, detail)))

    reconcile._refresh_running(home, {}, task)

    saved = home.load("t-x")
    assert saved["status"] == "waiting"
    assert saved["status_detail"] == "waiting: stage-3 checkpoint"
    assert saved["wait"]["note"] == "stage-3 checkpoint"
    assert saved["wait"]["since"] and saved["wait"]["last_polled"] is None
    # gate must NOT have been evaluated, no gate_result written, no attempt burned
    assert checked == []
    assert saved["gate_result"] is None
    assert saved["gate_failures"] == 0
    assert notes == [("waiting", "stage-3 checkpoint")]
    # sidecar consumed
    assert not home.wait_path("t-x").exists()


def test_stale_sidecar_is_ignored_and_cleaned(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    _write_wait(home, "t-x", attempt=99)  # from some other attempt
    monkeypatch.setattr(runtime.Worker, "attach", classmethod(lambda cls, h, t: _FakeWorker()))
    monkeypatch.setattr(gates, "check", lambda t, ws: Verdict(True, "shell_ok rc=0"))
    monkeypatch.setattr(reconcile, "notify", lambda *a, **k: None)

    reconcile._refresh_running(home, {}, task)

    saved = home.load("t-x")
    assert saved["status"] == "done"          # fell through to the gate as normal
    assert "wait" not in saved
    assert not home.wait_path("t-x").exists()  # stale sidecar deleted at consume time


def test_dispatch_clears_stale_sidecar(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path, status="queued")
    task["attempts"].clear()
    home.save(task)
    home.spec_path("t-x").write_text("do the thing")
    _write_wait(home, "t-x", attempt=1)
    monkeypatch.setattr(runtime.Worker, "spawn",
                        classmethod(lambda cls, h, t, prompt, cfg, resume=None, output_schema=None: _FakeWorker()))

    reconcile._dispatch(home, {}, task)

    assert not home.wait_path("t-x").exists()


# -- waking --

def _waiting_task(tmp_path, since_minutes_ago=0, last_polled=None, timeout_minutes=720):
    home, task = _home_task(tmp_path, status="waiting")
    since = (datetime.now() - timedelta(minutes=since_minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    task["wait"] = {"until_shell": "probe", "note": "pod pipeline",
                    "timeout_minutes": timeout_minutes, "requested_at": since,
                    "attempt": 1, "since": since, "last_polled": last_polled}
    home.save(task)
    return home, task


def _fake_run(rc, out="", err=""):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)
    return run


def test_wake_probe_fails_stays_waiting(tmp_path, monkeypatch):
    home, task = _waiting_task(tmp_path)
    seen = _capture_resume(monkeypatch)
    monkeypatch.setattr(reconcile.subprocess, "run", _fake_run(1, err="not yet"))

    reconcile._maybe_wake(home, {}, task)

    assert "resume" not in seen              # not resumed
    saved = home.load("t-x")
    assert saved["status"] == "waiting"
    assert saved["wait"]["last_polled"] is not None  # poll timestamp updated


def test_wake_probe_throttled(tmp_path, monkeypatch):
    # polled seconds ago; wait_poll_seconds default 60 → probe must NOT run
    home, task = _waiting_task(tmp_path, last_polled=now_iso())
    seen = _capture_resume(monkeypatch)
    ran = []
    monkeypatch.setattr(reconcile.subprocess, "run",
                        lambda *a, **k: ran.append(1) or subprocess.CompletedProcess(a, 0))

    reconcile._maybe_wake(home, {}, task)

    assert ran == []          # throttled, probe never evaluated
    assert "resume" not in seen


def test_wake_probe_succeeds_resumes_same_session(tmp_path, monkeypatch):
    home, task = _waiting_task(tmp_path)
    seen = _capture_resume(monkeypatch)
    monkeypatch.setattr(reconcile.subprocess, "run", _fake_run(0, out="DONE marker present"))

    reconcile._maybe_wake(home, {}, task)

    assert seen["resume"] == "sess-1"        # resumed the SAME session
    assert "probe succeeded" in seen["text"]
    saved = home.load("t-x")
    assert saved["status"] == "running"
    assert "wait" not in saved               # live wait cleared
    assert saved["wait_history"][-1]["note"] == "pod pipeline"  # archived
    assert saved["gate_failures"] == 0       # waking burns no gate failure


def test_wake_timeout_resumes_with_timeout_message(tmp_path, monkeypatch):
    home, task = _waiting_task(tmp_path, since_minutes_ago=1000, timeout_minutes=720)
    seen = _capture_resume(monkeypatch)
    # probe would succeed, but timeout should fire first (checked before the probe)
    monkeypatch.setattr(reconcile.subprocess, "run", _fake_run(1))

    reconcile._maybe_wake(home, {}, task)

    assert seen["resume"] == "sess-1"
    assert "timed out" in seen["text"]
    saved = home.load("t-x")
    assert saved["status"] == "running"
    assert "wait" not in saved
    assert saved["wait_history"]


def test_user_message_wakes_waiting_task(tmp_path, monkeypatch):
    home, task = _waiting_task(tmp_path)
    home.post("t-x", "user", "actually, abort and summarize")
    seen = _capture_resume(monkeypatch)
    # probe not yet done — the user message should wake it regardless
    monkeypatch.setattr(reconcile.subprocess, "run", _fake_run(1))

    reconcile._maybe_wake(home, {}, task)

    assert seen["resume"] == "sess-1"
    assert "abort and summarize" in seen["text"]
    saved = home.load("t-x")
    assert saved["status"] == "running"
    assert "wait" not in saved


# -- attempt accounting --

def _refresh_with_gate(home, task, monkeypatch, passed):
    monkeypatch.setattr(runtime.Worker, "attach", classmethod(lambda cls, h, t: _FakeWorker()))
    monkeypatch.setattr(gates, "check", lambda t, ws: Verdict(passed, "shell_ok rc=%d" % (0 if passed else 1)))
    monkeypatch.setattr(reconcile, "notify", lambda *a, **k: None)
    _capture_resume(monkeypatch)
    reconcile._refresh_running(home, {}, task)


def test_gate_fail_increments_gate_failures(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    _refresh_with_gate(home, task, monkeypatch, passed=False)
    assert home.load("t-x")["gate_failures"] == 1
    assert home.load("t-x")["status"] == "running"  # resumed, not failed


def test_task_fails_only_after_max_gate_failures(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    task["max_attempts"] = 2
    home.save(task)
    _refresh_with_gate(home, task, monkeypatch, passed=False)
    assert home.load("t-x")["status"] == "running"
    task = home.load("t-x")
    task["status"] = "running"
    home.save(task)
    _refresh_with_gate(home, task, monkeypatch, passed=False)
    saved = home.load("t-x")
    assert saved["gate_failures"] == 2
    assert saved["status"] == "failed"
    assert "2 gate-checked attempts" in saved["status_detail"]


def test_waiting_resume_does_not_burn_gate_failure(tmp_path, monkeypatch):
    home, task = _waiting_task(tmp_path)
    _capture_resume(monkeypatch)
    monkeypatch.setattr(reconcile.subprocess, "run", _fake_run(0))
    reconcile._maybe_wake(home, {}, task)
    assert home.load("t-x")["gate_failures"] == 0


def test_blocked_resume_does_not_burn_gate_failure(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path, status="blocked")
    home.post("t-x", "user", "here is your answer")
    _capture_resume(monkeypatch)
    reconcile._maybe_unblock(home, {}, task)
    assert home.load("t-x")["gate_failures"] == 0
    assert home.load("t-x")["status"] == "running"


# -- scheduling / api surface --

def test_waiting_not_counted_in_active_concurrency(tmp_path, monkeypatch):
    home, _ = _home_task(tmp_path, status="waiting")
    # a queued task must dispatch even while another task is waiting (waiting
    # holds no slot). concurrency=1: if waiting counted, nothing would dispatch.
    q = new_task("t-q", "q", {"kind": "always"}, {"usd": 10, "wall_minutes": 60},
                 {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    home.save(q)
    home.spec_path("t-q").write_text("go")

    dispatched = []
    monkeypatch.setattr(reconcile, "_dispatch",
                        lambda h, c, t: dispatched.append(t["id"]))
    # keep the waiting task inert this tick
    monkeypatch.setattr(reconcile, "_maybe_wake", lambda *a, **k: None)

    reconcile.tick(home, {"concurrency": 1})

    assert "t-q" in dispatched


def test_active_includes_waiting():
    assert "waiting" in ACTIVE


def test_ask_refuses_waiting_task(tmp_path):
    home, task = _home_task(tmp_path, status="waiting")
    pool = api.Pool(home.root)
    with pytest.raises(api.TaskFailed):
        import asyncio
        asyncio.run(pool.ask("t-x", "status?"))


def test_wait_sidecar_does_not_break_task_listing(tmp_path):
    # regression: <tid>.wait.json matches a bare t-*.json glob; Home.tasks()
    # must skip sidecars instead of KeyError'ing on the missing 'created'.
    home, task = _home_task(tmp_path)
    home.wait_path("t-x").write_text(json.dumps(
        {"until_shell": "true", "note": "", "attempt": 1,
         "requested_at": now_iso(), "timeout_minutes": 60}))
    listed = home.tasks()
    assert [t["id"] for t in listed] == ["t-x"]
