"""Unit tests for the orchestration skeleton (stage / gate / live_dashboard /
headless_handoff). Async tests drive their own loop via asyncio.run so there's no
pytest-asyncio dependency."""
import asyncio
import json

from stagehand import pipeline
from stagehand.monitor import monitor
from stagehand.pipeline import stage, gate, live_dashboard, headless_handoff


# ---- stage ---------------------------------------------------------------- #
def test_stage_preserves_unit_order():
    async def fn(u):
        await asyncio.sleep(0.01 * (5 - u))   # later units finish first...
        return u * 10
    out = asyncio.run(stage([0, 1, 2, 3], fn, concurrency=4))
    assert out == [0, 10, 20, 30]             # ...but results stay in unit order


def test_stage_bounds_concurrency():
    live = 0
    peak = 0

    async def fn(_u):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return None

    asyncio.run(stage(range(10), fn, concurrency=3))
    assert peak <= 3


def test_stage_backstops_a_raise_into_a_returned_exception():
    async def fn(u):
        if u == 1:
            raise ValueError("boom")
        return u
    out = asyncio.run(stage([0, 1, 2], fn, concurrency=2))
    assert out[0] == 0 and out[2] == 2
    assert isinstance(out[1], ValueError)     # the batch isn't cancelled


# ---- gate ----------------------------------------------------------------- #
def test_gate_partitions_and_marks_failed_monitor(tmp_path):
    # two units, each with a real monitor file; one will flunk the gate
    for name in ("good", "bad"):
        with monitor(name, 1, tmp_path / name / "p.progress.json", min_interval=0) as m:
            m.update()
    results = [{"name": "good", "ok": True}, {"name": "bad", "ok": False}]

    def predicate(r):
        return (r["ok"], [] if r["ok"] else ["it is bad"])

    passed, failed = gate(results, predicate,
                          monitor_path=lambda r: tmp_path / r["name"] / "p.progress.json")
    assert [p["name"] for p in passed] == ["good"]
    assert failed[0][0]["name"] == "bad" and failed[0][1] == ["it is bad"]
    # the bad unit's monitor file was patched done -> failed with the gate reason
    s = json.loads((tmp_path / "bad" / "p.progress.json").read_text())
    assert s["state"] == "failed" and "it is bad" in s["extra"]["error"]
    # the good unit's monitor is untouched
    assert json.loads((tmp_path / "good" / "p.progress.json").read_text())["state"] == "done"


# ---- live_dashboard ------------------------------------------------------- #
def test_live_dashboard_writes_and_finalizes(tmp_path):
    async def body():
        async with live_dashboard(tmp_path, title="t", interval=0.05) as html_path:
            with monitor("u", 1, tmp_path / "u.progress.json", parent=None, min_interval=0) as m:
                await asyncio.sleep(0.08)     # let the writer tick at least once
                m.update()
            await asyncio.sleep(0.08)
            return html_path
        # context exit forces a final render

    html_path = asyncio.run(body())
    assert html_path.exists()
    html = html_path.read_text()
    assert "t" in html and "u" in html and "1 done" in html   # terminal state captured


# ---- headless_handoff ----------------------------------------------------- #
def test_headless_handoff_builds_expected_argv(monkeypatch):
    captured = {}

    class _Proc:
        async def wait(self):
            return 0

    async def fake_exec(*argv, cwd=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(pipeline.asyncio, "create_subprocess_exec", fake_exec)
    rc = asyncio.run(headless_handoff("do the thing", cwd="/repo",
                                      allowed_tools=("Workflow", "Bash")))
    assert rc == 0
    argv = captured["argv"]
    assert argv[0] == "claude" and "-p" in argv and "do the thing" in argv
    assert "Workflow,Bash" in argv          # allowlist joined with commas
    assert captured["cwd"] == "/repo"
