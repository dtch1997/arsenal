"""Serve a running `Flow`'s monitor tree as one live, auto-refreshing HTML page.

`live_dashboard` polls the monitor tree a flow writes and re-renders the page on
an interval until you're done. The execution engine itself lives in `engine`.

    async with live_dashboard(flow.runs_dir, title="my sweep") as status_html:
        await flow.run()
"""
from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from .monitor import read_monitors, read_graph
from .dashboard import render_dashboard, default_note


@asynccontextmanager
async def live_dashboard(runs_dir, *, title="stagehand", note_fn=default_note,
                         out="status.html", interval=3.0, refresh=5, started=None):
    """Poll the monitor tree under `runs_dir` into `out` until the body exits.

    Spawns a background writer that re-renders `runs_dir/out` every `interval`
    seconds, and tears it down (with one final render) on exit — so the dashboard
    always reflects the terminal state. Yields the path to the rendered HTML file;
    serve it however you like (e.g. a static server behind a tunnel).
    """
    rd = Path(runs_dir)
    rd.mkdir(parents=True, exist_ok=True)
    start = time.time() if started is None else started
    stop = asyncio.Event()

    def _render():
        (rd / out).write_text(
            render_dashboard(read_monitors(rd), start, title=title,
                             note_fn=note_fn, refresh=refresh, graph=read_graph(rd)))

    async def writer():
        while True:
            _render()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue
        _render()   # final terminal-state render

    task = asyncio.create_task(writer())
    try:
        yield rd / out
    finally:
        stop.set()
        await task
