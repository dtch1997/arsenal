"""Orchestration skeleton: staged async pipelines with gates between them, a live
dashboard over the monitor tree, and a headless-Claude handoff at the end.

These are the patterns behind a real experiment sweep, with the domain logic pulled
out. The canonical shape is a *staircase* — a barrier-separated sequence of stages,
each followed by a gate that drops unhealthy units before the next (expensive) stage:

    async with live_dashboard("runs", title="my sweep") as status_html:
        trained = await stage(cells, train_one, concurrency=4)          # barrier
        healthy, failed = gate(trained, gate_train,                     # drop the dead
                               monitor_path=lambda r: r["dir"] / "train.progress.json")
        evals = await stage(fanout(healthy), eval_one, concurrency=8)   # barrier
        write_manifest(evals)
        await headless_handoff(prompt, cwd=repo)                        # optional

Why barriers (gather) rather than a free-for-all: a gate needs the *whole* previous
stage before it can decide what survives, and the manifest is the single handoff
artifact. Within a stage, `concurrency` bounds how many units run at once.
"""
from __future__ import annotations
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from .monitor import read_monitors, mark
from .dashboard import render_dashboard, default_note


async def stage(units, fn, *, concurrency=1):
    """Run `fn(unit)` for every unit, at most `concurrency` at a time, then gather.

    This is the barrier: it returns only once every unit is finished, in unit order.
    `fn` is expected to capture its own failures and return a result object (so the
    gate can inspect it); as a backstop, if `fn` raises anyway the exception is
    returned *in place* of that unit's result rather than cancelling the batch.
    """
    sem = asyncio.Semaphore(concurrency)

    async def run(u):
        async with sem:
            try:
                return await fn(u)
            except Exception as e:   # backstop: don't let one unit abort the gather
                return e

    return await asyncio.gather(*(run(u) for u in units))


def gate(results, predicate, *, monitor_path=None):
    """Partition `results` by `predicate(result) -> (ok, issues)`.

    Returns `(passed, failed)`, where `passed` is the surviving results and `failed`
    is a list of `(result, issues)`. If `monitor_path` is given (a callable
    `result -> path | None`), each failed unit's monitor file is marked `failed` with
    the issues recorded — so a unit that passed its stage but flunked the gate still
    shows up red on the dashboard.
    """
    passed, failed = [], []
    for r in results:
        ok, issues = predicate(r)
        if ok:
            passed.append(r)
        else:
            failed.append((r, issues))
            if monitor_path is not None:
                p = monitor_path(r)
                if p is not None:
                    mark(p, state="failed", extra={"error": "gate: " + "; ".join(issues)})
    return passed, failed


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
                             note_fn=note_fn, refresh=refresh))

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


async def headless_handoff(prompt, *, cwd=".",
                           allowed_tools=("Workflow", "Bash", "Read", "Write", "Edit"),
                           permission_mode="acceptEdits", claude_bin="claude"):
    """Hand a finished pipeline (e.g. its manifest) to a headless ``claude -p``.

    Runs Claude non-interactively with `prompt` and the given tool allowlist, in
    `cwd`. Returns the process exit code. The usual use is a one-shot "invoke the
    Workflow tool on this manifest and report its summary" handoff after the
    manifest is written.
    """
    proc = await asyncio.create_subprocess_exec(
        claude_bin, "-p", prompt,
        "--allowed-tools", ",".join(allowed_tools),
        "--permission-mode", permission_mode, cwd=cwd)
    return await proc.wait()
