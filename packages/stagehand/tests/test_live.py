"""Unit tests for the live-dashboard helper (live_dashboard).
The async test drives its own loop via asyncio.run so there's no pytest-asyncio
dependency."""
import asyncio

from stagehand.monitor import monitor
from stagehand.live import live_dashboard


# ---- live_dashboard ------------------------------------------------------- #
def test_live_dashboard_writes_and_finalizes(tmp_path):
    async def body():
        async with live_dashboard(tmp_path, title="t", interval=0.05) as html_path:
            with monitor("u", 1, tmp_path / "u.progress.json", parent=None,
                         min_interval=0, cleanup=False) as m:
                await asyncio.sleep(0.08)     # let the writer tick at least once
                m.update()
            await asyncio.sleep(0.08)
            return html_path
        # context exit forces a final render

    html_path = asyncio.run(body())
    assert html_path.exists()
    html = html_path.read_text()
    assert "t" in html and "u" in html and "1 done" in html   # terminal state captured
