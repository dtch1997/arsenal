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
import inspect
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


# --- calling convention for fn under the combinators ----------------------- #
# `stage` calls a unit-fn as `fn(unit)`. The combinators below give a unit more
# context (which attempt this is, what feedback the last attempt produced) by
# passing `attempt=` / `feedback=` *only if* `fn` accepts them — so a plain
# `fn(unit)` keeps working unchanged, while an opt-in
# `async def fn(unit, *, attempt=0, feedback=None)` gets the extra context.
async def _call(fn, unit, **context):
    """Await ``fn(unit, **kw)`` with the subset of `context` that `fn` accepts.

    A `fn` declaring ``**kwargs`` receives all of `context`; otherwise only the
    named parameters it actually has are passed. Functions whose signature can't
    be introspected (some builtins/C callables) get the full `context`.
    """
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return await fn(unit, **context)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kw = context
    else:
        kw = {k: v for k, v in context.items() if k in params}
    return await fn(unit, **kw)


def best_of(fn, n, *, judge=None, score=None, concurrency=None, monitor_path=None):
    """Wrap `fn` into a unit-fn that runs `n` attempts and returns the best one.

    The returned ``best_of_unit(unit)`` runs `n` independent attempts of
    `fn(unit, attempt=i)` for ``i`` in ``0..n-1`` (use `attempt` to vary a seed /
    sampling so the tries actually differ; plain `fn(unit)` works too). Attempts
    run concurrently (`concurrency`, default = all `n`).

    Pick the winner with exactly one of:
      - ``judge(results) -> int`` : an async-or-sync chooser returning the index
        of the best result (e.g. an LLM ranking the attempts).
      - ``score(result) -> float`` : keep the highest-scoring attempt (ties go to
        the earliest attempt).

    Attempts that raised are dropped before judging/scoring; if *every* attempt
    raised, the first exception is returned (so `stage`/`gate` still see the
    failure, matching `stage`'s backstop). If `monitor_path` (a callable
    ``result -> path | None``) is given, the losing attempts' monitor files are
    marked `failed` ("best_of: not selected") so the whole fan-out is visible on
    the dashboard; the winner is left as it finished.

    Drop it straight into `stage` to fan out every unit::

        await stage(units, best_of(solve, n=4, score=reward), concurrency=2)
    """
    if (judge is None) == (score is None):
        raise ValueError("best_of needs exactly one of judge= or score=")

    async def best_of_unit(unit, **context):
        results = await stage(
            range(n),
            lambda i: _call(fn, unit, **{**context, "attempt": i}),
            concurrency=concurrency or n)
        ok = [r for r in results if not isinstance(r, BaseException)]
        if not ok:
            return next(r for r in results if isinstance(r, BaseException))

        if judge is not None:
            win = judge(ok)
            if inspect.isawaitable(win):
                win = await win
            if not isinstance(win, int) or not 0 <= win < len(ok):
                raise ValueError(f"best_of judge must return an index in "
                                 f"0..{len(ok)-1}, got {win!r}")
        else:
            win = max(range(len(ok)), key=lambda k: score(ok[k]))
        winner = ok[win]

        if monitor_path is not None:
            for k, r in enumerate(ok):
                if k == win:
                    continue
                p = monitor_path(r)
                if p is not None:
                    reason = (f"score={score(r):.4g} < {score(winner):.4g}"
                              if score is not None else "judge")
                    mark(p, state="failed",
                         extra={"error": "best_of: not selected (" + reason + ")"})
        return winner

    return best_of_unit


def with_retry(fn, *, check, max_attempts=3, feedback=None, monitor_path=None):
    """Wrap `fn` into a unit-fn that retries with feedback until `check` passes.

    The returned ``retry_unit(unit)`` runs `fn(unit)`; if the result fails
    `check`, it retries, feeding the previous try's feedback back in, until it
    passes or `max_attempts` is reached.

    `check(result) -> (ok, issues)` has the same shape as a `gate` predicate:
    ``ok=False`` (or `fn` raising) triggers a retry. Each try is
    `fn(unit, attempt=i, feedback=fb)` — `feedback` is None on the first try and
    the previous try's feedback thereafter; plain `fn(unit)` ignores both. By
    default `feedback` is the `issues` list from `check`; pass
    ``feedback(result, issues) -> any`` to transform it (e.g. into a prompt).

    Returns the first passing result, or — if attempts run out — the last failing
    result (or the last exception object, matching `stage`'s backstop). Superseded
    attempts are marked `failed` on the dashboard if `monitor_path` is given.

    Retries are sequential per unit (each needs the prior feedback); fan units out
    across `stage` for cross-unit concurrency::

        await stage(units, with_retry(solve, check=passes), concurrency=4)
    """
    async def retry_unit(unit, **context):
        fb = context.get("feedback")
        last = None
        for attempt in range(max_attempts):
            try:
                result = await _call(fn, unit,
                                     **{**context, "attempt": attempt, "feedback": fb})
                ok, issues = check(result)
            except Exception as e:        # a raise is a failure too: feed it back
                result, ok, issues = e, False, [repr(e)]
            if ok:
                return result
            last = result
            fb = feedback(result, issues) if feedback is not None else issues
            if monitor_path is not None and attempt < max_attempts - 1 \
                    and not isinstance(result, BaseException):
                p = monitor_path(result)
                if p is not None:
                    mark(p, state="failed",
                         extra={"error": f"retry: superseded (attempt {attempt}): "
                                         + "; ".join(map(str, issues))})
        return last

    return retry_unit


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
