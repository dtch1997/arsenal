"""Unit tests for the serving/handoff helpers (live_dashboard / headless_handoff).
Async tests drive their own loop via asyncio.run so there's no pytest-asyncio
dependency."""
import asyncio

from stagehand import pipeline
from stagehand.monitor import monitor
from stagehand.pipeline import live_dashboard, headless_handoff


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
