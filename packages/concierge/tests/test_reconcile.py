"""gate_result is persisted on both the pass and fail paths; the fail-resume
message discourages placeholder shipping."""
from concierge import gates, reconcile, runtime
from concierge.gates import Verdict
from concierge.records import Home, new_task, now_iso
from concierge.runtime import WorkerState


def _home_task(tmp_path):
    home = Home(tmp_path / "home")
    task = new_task("t-x", "t", {"kind": "always"}, {"usd": 10, "wall_minutes": 60},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    task["status"] = "running"
    task["attempts"].append({"n": 1, "pid": 1, "started": now_iso(),
                             "session_id": "sess-1", "cost_usd": 0.5,
                             "result": None, "log": "logs/t-x/attempt-1"})
    home.save(task)
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


def _patch(monkeypatch, verdict, capture=None):
    monkeypatch.setattr(runtime.Worker, "attach", classmethod(lambda cls, home, task: _FakeWorker()))
    monkeypatch.setattr(gates, "check", lambda task, ws: verdict)
    if capture is not None:
        def fake_spawn(cls, home, task, text, cfg, resume=None, output_schema=None):
            capture["text"] = text
            task["attempts"].append({"n": len(task["attempts"]) + 1, "pid": 2,
                                     "started": now_iso(), "session_id": None,
                                     "cost_usd": None, "result": None, "log": "x"})
            return _FakeWorker()
        monkeypatch.setattr(runtime.Worker, "spawn", classmethod(fake_spawn))


def test_gate_result_persisted_on_pass(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    _patch(monkeypatch, Verdict(True, "shell_ok rc=0: pytest"))

    reconcile._refresh_running(home, {}, task)

    saved = home.load("t-x")
    assert saved["status"] == "done"
    assert saved["gate_result"]["passed"] is True
    assert saved["gate_result"]["detail"] == "shell_ok rc=0: pytest"
    assert saved["gate_result"]["checked_at"]


def test_gate_result_persisted_on_fail(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    capture = {}
    _patch(monkeypatch, Verdict(False, "shell_ok rc=1: boom"), capture)

    reconcile._refresh_running(home, {}, task)

    saved = home.load("t-x")
    assert saved["gate_result"]["passed"] is False
    assert saved["gate_result"]["detail"] == "shell_ok rc=1: boom"


def test_fail_resume_message_discourages_placeholders(tmp_path, monkeypatch):
    home, task = _home_task(tmp_path)
    capture = {}
    _patch(monkeypatch, Verdict(False, "shell_ok rc=1: boom"), capture)

    reconcile._refresh_running(home, {}, task)

    assert "do NOT ship placeholder" in capture["text"]
    assert "run_in_background" in capture["text"]
