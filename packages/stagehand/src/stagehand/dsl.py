"""dsl — an imperative-reading surface over the `engine`.

You write what looks like straight-line code; it builds the DAG, and `run()`
executes it on the engine (so you keep the scheduler, monitors, `stop_when`, and
dynamic fan-out). The pieces are *lazy*: `do(...)` returns a placeholder
**handle**, nothing runs until `run()`.

    with flow("runs", concurrency=8):
        ckpt  = do(train, cell)                    # a task, no deps
        good  = do(check, ckpt)                    # handle arg ⇒ dependency
        best  = fanout(solve, good, n=4, score=s)  # same shape as do
        fixed = retry(format, best, check=parses)  # same shape as do
        do(report, after=[fixed])                  # `wait(A); do(B)` — ordering only
        await run(stop_when=lambda s: s.done >= 100)
    print(ckpt.result)                             # filled in after the run

Dependencies are inferred from the handles you pass as arguments (the engine
substitutes each handle with its result). `do`/`fanout`/`retry` all return a
handle and have the same shape. Because a handle is a placeholder, you can't
branch on its value while building — do result-dependent control flow *inside* a
node fn (it's a normal coroutine: plain `await`/`if`/loops), or with the engine's
`filter` / `expand` on a `Flow`.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar

from .engine import Flow, best_of, with_retry

_current: ContextVar = ContextVar("stagehand_flow", default=None)


def current() -> Flow:
    """The `Flow` of the innermost active `with flow(...)` block."""
    f = _current.get()
    if f is None:
        raise RuntimeError("do()/run() must be used inside a `with flow(...):` block")
    return f


@contextmanager
def flow(runs_dir=None, *, concurrency=8, title="flow"):
    """Open a `Flow` and make it the current one for `do`/`fanout`/`retry`/`run`.

    Yields the underlying `Flow`, so you can drop down to `Flow.map`/`filter`/
    `reduce`/`expand` for the parts the per-task DSL doesn't cover.
    """
    f = Flow(runs_dir, concurrency=concurrency, title=title)
    token = _current.set(f)
    try:
        yield f
    finally:
        _current.reset(token)


def do(fn, *args, after=(), name=None, **kwargs):
    """A single task running `fn(*args, **kwargs)`. Any handle in the arguments
    becomes a dependency (substituted with its result); `after` adds ordering-only
    dependencies. Returns a one-task handle."""
    return current().spawn(fn, args, kwargs, name=name, after=after)


def fanout(fn, unit, *, n, judge=None, score=None, after=(), name=None):
    """Fan `unit` out into `n` attempts of `fn` and keep the best (same shape as
    `do`). Winner picked by `judge(results) -> index` or `score(result) -> float`.
    `unit` may be a handle (its result is the input). Returns a one-task handle."""
    policy = best_of(fn, n, judge=judge, score=score)
    return current().spawn(policy, (unit,), name=name or _name(fn, "fanout"),
                           after=after, type_fn=fn)


def retry(fn, unit, *, check, max_attempts=3, feedback=None, after=(), name=None):
    """Run `fn(unit)`, retrying with feedback until `check` passes (same shape as
    `do`). `unit` may be a handle. Returns a one-task handle."""
    policy = with_retry(fn, check=check, max_attempts=max_attempts, feedback=feedback)
    return current().spawn(policy, (unit,), name=name or _name(fn, "retry"),
                           after=after, type_fn=fn)


def each(fn, items, **kwargs):
    """`[do(fn, x, **kwargs) for x in items]` — fan a list out into one task each."""
    return [do(fn, x, **kwargs) for x in items]


async def run(*, stop_when=None):
    """Run the current `with flow(...)` graph; returns the final `RunState`."""
    return await current().run(stop_when=stop_when)


def _name(fn, suffix):
    return f"{getattr(fn, '__name__', 'task')}.{suffix}"
