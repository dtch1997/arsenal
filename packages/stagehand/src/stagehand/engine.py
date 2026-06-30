"""engine — a declarative DAG executor for experiment sweeps.

You declare *what* work needs doing and how the pieces depend on each other; the
engine figures out *how* to run it — scheduling everything that's ready up to a
concurrency cap, streaming results from one node into the next without barriers,
fanning out dynamically, and stopping early when an exit criterion is met.

    flow = Flow("runs", concurrency=8)
    trained = flow.map("train", cells, train_one)          # one task per cell
    healthy = flow.filter("gate", trained, is_healthy)     # survivors stream on
    evals   = flow.map("eval", healthy, eval_one)          # eval_i waits on train_i only
    best    = flow.reduce("pick", evals, choose_best)      # the *only* barrier
    await flow.run(stop_when=lambda s: s.done >= 100)

The kernel is a dynamic per-task DAG: a stage like ``map`` is just a *template*
that stamps out one task per item and wires its dependencies, so ``eval(cell_1)``
starts the moment ``train(cell_1)`` finishes while ``train(cell_5)`` is still
going. Barriers exist only where a node genuinely needs its whole upstream
collection — that's what ``reduce`` is for. Fan-out whose size isn't known until
runtime (train emits K checkpoints, eval each) is ``expand``; the raw escape hatch
for irregular graphs is ``add``.

Every task writes a `monitor` file under ``runs_dir/<node>/``, so the existing
``dashboard`` / ``serve`` render the live graph with no extra wiring:

    async with live_dashboard(flow.runs_dir, title="my sweep"):
        await flow.run()
"""
from __future__ import annotations
import asyncio
import inspect
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Generic, TypeVar, Union,
                    get_args, get_origin, get_type_hints)

from .monitor import monitor as _monitor, mark
from ._log import log

# The monitor of the task currently running on this asyncio task — lets a step
# stream its own live progress (e.g. an agent's action/tokens) to the dashboard
# via `current_monitor()` without threading the monitor through every fn.
_task_monitor: ContextVar = ContextVar("stagehand_task_monitor", default=None)


def current_monitor():
    """The running task's `Monitor` (or None if the flow has no `runs_dir`).

    Call `current_monitor().set(**fields)` inside a step to push live progress to
    the dashboard — used by agent steps to surface status/last-action/tokens."""
    return _task_monitor.get()

PENDING, RUNNING, DONE, FAILED, SKIPPED = (
    "pending", "running", "done", "failed", "skipped")
_TERMINAL = frozenset((DONE, FAILED, SKIPPED))

T = TypeVar("T")


class _Filtered(Exception):
    """Raised inside a `filter` node when an item is pruned; not a real failure."""
    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__("; ".join(map(str, self.issues)))


class FlowCheckError(Exception):
    """Raised by `Flow.check()` when the declared graph can't run start to end —
    a missing dependency, a cycle, or an edge whose types don't line up."""
    def __init__(self, issues):
        self.issues = list(issues)
        super().__init__("flow check failed:\n  - " + "\n  - ".join(self.issues))


@dataclass
class Task:
    """One node in the DAG: a coroutine that runs once its `deps` are satisfied.

    `run(results)` reads its dependencies' results out of the flow's `results`
    dict and returns this task's result. `gather` tasks (reduce) wait for *all*
    deps to reach a terminal state and run over the survivors; ordinary tasks run
    only when every dep is `done`, and are skipped if any dep failed/was skipped.
    """
    id: str
    node: str
    deps: tuple
    run: object                       # async (results) -> result, or None (value task)
    gather: bool = False
    on_done: object = None            # optional (result) -> None hook (expand)
    state: str = PENDING
    result: object = None
    error: object = None


class Handle(Generic[T]):
    """The output collection of a node — a *growing* list of task ids plus
    subscribers fired per id. Downstream nodes subscribe to mint one task per
    upstream item as it appears, so static and dynamic fan-out share one path.

    Parameterized by the type of a single result element (`elem_type`): a
    `Handle[Model]` is a node producing `Model`s. The engine fills `elem_type`
    from each step's return annotation so `flow.check()` can verify edges and
    `.result` / `.results()` are typed in your editor.
    """
    def __init__(self, flow, node, *, kind="many", elem_type=Any):
        self.flow = flow
        self.node = node
        self.kind = kind                 # "one" (single task) | "many" (collection)
        self.elem_type = elem_type       # type of one result element (Any if unknown)
        self.ids: list[str] = []
        self.closed = False
        self._on_id: list = []
        self._on_close: list = []

    def add(self, tid):
        self.ids.append(tid)
        for f in list(self._on_id):
            f(tid)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for f in list(self._on_close):
            f()

    def subscribe(self, *, on_id=None, on_close=None):
        if on_id is not None:
            for tid in list(self.ids):       # replay ids already present
                on_id(tid)
            self._on_id.append(on_id)
        if on_close is not None:
            if self.closed:
                on_close()
            else:
                self._on_close.append(on_close)

    def results(self) -> list[T]:
        """Results of this node's tasks that finished `done` (call after `run`)."""
        return [self.flow.results[i] for i in self.ids if i in self.flow.results]

    @property
    def result(self) -> T:
        """The single result of a one-task handle (`do`/`reduce`/`add`); for a
        collection handle, the list of done results."""
        if self.kind == "one":
            return self.flow.results.get(self.ids[0]) if self.ids else None
        return self.results()


class RunState:
    """Live view passed to `stop_when` — accumulated results and state counts."""
    def __init__(self, flow):
        self.flow = flow

    @property
    def results(self):
        return [t.result for t in self.flow.tasks.values()
                if t.state == DONE and t.run is not None]

    def _count(self, st):
        return sum(1 for t in self.flow.tasks.values()
                   if t.state == st and t.run is not None)

    @property
    def done(self):
        return self._count(DONE)

    @property
    def failed(self):
        return self._count(FAILED)

    @property
    def skipped(self):
        return self._count(SKIPPED)


class Flow:
    """A DAG of work. Declare nodes with `map`/`filter`/`reduce`/`expand`/`add`,
    then `await flow.run()`."""

    def __init__(self, runs_dir=None, *, concurrency=8, title="flow"):
        self.runs_dir = Path(runs_dir) if runs_dir is not None else None
        self.concurrency = concurrency
        self.title = title
        self.tasks: dict[str, Task] = {}
        self.results: dict[str, object] = {}
        self._counter: dict[str, int] = {}
        self._node_conc: dict[str, int] = {}
        self._on_done: dict[str, list] = {}
        self._checks: list = []          # (edge_label, expected_type, provided_type)
        # topology, for the dashboard to render the actual graph (not just progress)
        self._node_kind: dict[str, str] = {}    # node -> map|filter|reduce|expand|gather|task
        self._edges: set = set()                # (src_node, dst_node) node-level edges
        self._start = None

    # ---- ids ------------------------------------------------------------- #
    def _tid(self, node):
        i = self._counter.get(node, 0)
        self._counter[node] = i + 1
        return f"{node}/{i}"

    # ---- surface --------------------------------------------------------- #
    def map(self, node, source, fn, *, concurrency=None):
        """One task per item of `source` (a static iterable or an upstream handle),
        each running `fn(item)`; results stream into the returned handle."""
        self._node_kind[node] = "map"
        if concurrency:
            self._node_conc[node] = concurrency
        out = Handle(self, node, elem_type=_ret(fn))

        def mint_dep(up_id):
            tid = self._tid(node)
            async def run(results, up_id=up_id):
                return await _call(fn, results[up_id])
            self.tasks[tid] = Task(tid, node, (up_id,), run)
            out.add(tid)

        if isinstance(source, Handle):
            self._edges.add((source.node, node))
            self._record(f"map {node!r} <- {source.node!r}", _param0(fn), source.elem_type)
            source.subscribe(on_id=mint_dep, on_close=out.close)
        else:
            for item in source:
                tid = self._tid(node)
                async def run(results, item=item):
                    return await _call(fn, item)
                self.tasks[tid] = Task(tid, node, (), run)
                out.add(tid)
            out.close()
        return out

    def filter(self, node, source, pred, *, concurrency=None):
        """Like `map`, but each item runs `pred` and only survivors propagate.

        `pred(item) -> bool` or `-> (ok, issues)` (a gate predicate). A pruned item
        is marked `failed` ("filtered: …") on the dashboard, and any task that
        depended on it is skipped.
        """
        self._node_kind[node] = "filter"
        if concurrency:
            self._node_conc[node] = concurrency
        src_elem = source.elem_type if isinstance(source, Handle) else Any
        out = Handle(self, node, elem_type=src_elem)   # filter passes items through

        def mint_dep(up_id):
            tid = self._tid(node)
            async def run(results, up_id=up_id):
                return _apply_pred(pred, results[up_id])
            self.tasks[tid] = Task(tid, node, (up_id,), run)
            out.add(tid)

        if isinstance(source, Handle):
            self._edges.add((source.node, node))
            self._record(f"filter {node!r} <- {source.node!r}", _param0(pred), src_elem)
            source.subscribe(on_id=mint_dep, on_close=out.close)
        else:
            for item in source:
                tid = self._tid(node)
                async def run(results, item=item):
                    return _apply_pred(pred, item)
                self.tasks[tid] = Task(tid, node, (), run)
                out.add(tid)
            out.close()
        return out

    def reduce(self, node, source, fn):
        """A single barrier task that runs `fn(list_of_results)` once *all* of
        `source`'s tasks are terminal — over the survivors (skipped/failed deps are
        dropped from the list). The one place a barrier is intended."""
        self._node_kind[node] = "reduce"
        out = Handle(self, node, kind="one", elem_type=_ret(fn))
        if isinstance(source, Handle):
            self._edges.add((source.node, node))
            self._record(f"reduce {node!r} <- {source.node!r}",
                         _param0(fn), list[source.elem_type])

        def make(dep_ids):
            tid = self._tid(node)
            async def run(results, dep_ids=tuple(dep_ids)):
                vals = [results[i] for i in dep_ids if i in results]
                r = fn(vals)
                return await r if inspect.isawaitable(r) else r
            self.tasks[tid] = Task(tid, node, tuple(dep_ids), run, gather=True)
            out.add(tid)
            out.close()

        if isinstance(source, Handle):
            if source.closed:
                make(source.ids)
            else:
                source.subscribe(on_close=lambda: make(source.ids))
        else:
            items = list(source)
            tid = self._tid(node)
            async def run(results, items=items):
                r = fn(items)
                return await r if inspect.isawaitable(r) else r
            self.tasks[tid] = Task(tid, node, (), run)
            out.add(tid)
            out.close()
        return out

    def expand(self, node, source, fn):
        """Dynamic fan-out: for each upstream result, `fn(result) -> iterable` and
        each element becomes an item in the returned handle (so a downstream `map`
        runs per element). Use when the fan-out width isn't known until runtime."""
        if not isinstance(source, Handle):
            raise TypeError("expand needs an upstream handle as its source")
        self._node_kind[node] = "expand"
        out = Handle(self, node, elem_type=_elem_of(_ret(fn)))
        self._edges.add((source.node, node))
        self._record(f"expand {node!r} <- {source.node!r}", _param0(fn), source.elem_type)
        st = {"open": 0, "closed": False}

        def on_id(up_id):
            st["open"] += 1
            def cb(result):
                for e in fn(result):
                    cid = self._tid(node)
                    # carry the upstream task id as a (pre-satisfied) dep so the
                    # dashboard can trace item lineage across the fan-out
                    self.tasks[cid] = Task(cid, node, (up_id,), None,
                                           state=DONE, result=e)
                    self.results[cid] = e
                    out.add(cid)
                st["open"] -= 1
                if st["closed"] and st["open"] == 0:
                    out.close()
            self._on_done.setdefault(up_id, []).append(cb)

        def on_close():
            st["closed"] = True
            if st["open"] == 0:
                out.close()

        source.subscribe(on_id=on_id, on_close=on_close)
        return out

    def add(self, id, fn, *, deps=(), node=None):
        """Raw escape hatch: a single task `id` running `fn(*dep_results)` after the
        given dependency task ids. For irregular graphs the templates don't cover."""
        node = node or id
        deps = tuple(deps)
        self._node_kind.setdefault(node, "task")
        for d in deps:
            if d in self.tasks:
                self._edges.add((self.tasks[d].node, node))
        async def run(results, deps=deps, fn=fn):
            args = [results[d] for d in deps]
            r = fn(*args)
            return await r if inspect.isawaitable(r) else r
        self.tasks[id] = Task(id, node, deps, run)
        out = Handle(self, node, kind="one", elem_type=_ret(fn))
        out.add(id)
        out.close()
        return out

    def spawn(self, fn, args=(), kwargs=None, *, name=None, after=(), gather=None,
              type_fn=None):
        """Add a single task running `fn(*args, **kwargs)`, where any `Handle` found
        in `args`/`kwargs` (even nested in lists/tuples/dicts) becomes a dependency
        and is substituted with its result at run time. `after` is extra ordering-
        only dependencies (handles). Returns a one-task handle. This is the
        per-task primitive the `do` / `fanout` / `retry` DSL is built on.

        `type_fn` supplies the type annotations when `fn` is a wrapper (e.g. the
        `best_of` / `with_retry` policies wrap the user fn); defaults to `fn`.
        """
        kwargs = {} if kwargs is None else kwargs
        type_fn = type_fn or fn
        node = name or getattr(type_fn, "__name__", "task")
        arg_handles = list(_handles_in(args)) + list(_handles_in(kwargs))
        top_level = [a for a in (*args, *kwargs.values()) if isinstance(a, Handle)]
        nested = len(arg_handles) > len(top_level)   # handle(s) inside a list/dict arg
        dep_handles = arg_handles + list(after)
        dep_ids = tuple(i for h in dep_handles for i in h.ids)
        if gather is None:
            # gather (run over survivors once all deps are terminal) when aggregating
            # a collection — a "many" handle, or a list/dict of handles passed as one
            # arg; separate scalar handle args are all-required (skip if any fails).
            gather = nested or any(h.kind == "many" for h in dep_handles)
        self._node_kind.setdefault(node, "gather" if gather else "task")
        for h in dep_handles:
            self._edges.add((h.node, node))
        self._record_spawn(node, type_fn, args, kwargs)
        tid = self._tid(node)
        async def run(results, args=args, kwargs=kwargs, fn=fn):
            a = _resolve(args, results)
            kw = _resolve(kwargs, results)
            r = fn(*a, **kw)
            return await r if inspect.isawaitable(r) else r
        self.tasks[tid] = Task(tid, node, dep_ids, run, gather=gather)
        out = Handle(self, node, kind="one", elem_type=_ret(type_fn))
        out.add(tid)
        out.close()
        return out

    # ---- type checking ("compile") --------------------------------------- #
    def _record(self, label, expected, provided):
        self._checks.append((label, expected, provided))

    def _record_spawn(self, node, fn, args, kwargs):
        """Bind a `do`/`fanout`/`retry` call's handle args to `fn`'s params and
        record an edge type check for each top-level scalar handle argument."""
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        except TypeError:
            return
        hints = _hints(fn)
        for pname, val in bound.arguments.items():
            if isinstance(val, Handle) and val.kind == "one":
                self._record(f"do {node!r} arg {pname!r}",
                             hints.get(pname, Any), val.elem_type)

    def check(self):
        """Verify the declared graph can run start to end, or raise
        `FlowCheckError`. Checks every dependency exists, the graph is acyclic, and
        each edge's producer type is compatible with the consumer's input type
        (annotations are read from the step fns; unannotated == `Any`). Pure static
        analysis of the built graph — call it (or `run(check=True)`) before running.
        """
        issues = []
        for t in self.tasks.values():
            for d in t.deps:
                if d not in self.tasks:
                    issues.append(f"{t.id!r}: depends on missing task {d!r}")
        issues += self._find_cycle()
        for label, expected, provided in self._checks:
            if not _compatible(provided, expected):
                issues.append(f"{label}: expects {_tn(expected)}, got {_tn(provided)}")
        if issues:
            raise FlowCheckError(issues)

    def _find_cycle(self):
        color = {}      # 0=visiting, 1=done
        order = []
        def visit(tid):
            if color.get(tid) == 1:
                return
            if color.get(tid) == 0:
                order.append(tid)
                return [f"cycle through {' -> '.join(order + [tid])}"]
            color[tid] = 0
            order.append(tid)
            for d in self.tasks[tid].deps:
                if d in self.tasks:
                    found = visit(d)
                    if found:
                        return found
            color[tid] = 1
            order.pop()
            return []
        for tid in list(self.tasks):
            found = visit(tid)
            if found:
                return found
        return []

    # ---- scheduler ------------------------------------------------------- #
    async def run(self, *, stop_when=None, check=False):
        """Schedule the whole graph, streaming and bounded by `concurrency`.

        `stop_when(state) -> bool` is checked after every completion; when it
        returns truthy, in-flight tasks are cancelled and the run stops early.
        With `check=True`, run `self.check()` first (raise before doing any work if
        the graph is malformed). Returns the final `RunState`.
        """
        if check:
            self.check()
        self._start = time.time()
        if self.runs_dir is not None:
            self._flush_graph()
        self._gsem = asyncio.Semaphore(self.concurrency)
        self._node_sems = {n: asyncio.Semaphore(c)
                           for n, c in self._node_conc.items()}
        state = RunState(self)
        running: set = set()
        stopped = False
        log.info("flow %r starting — %d tasks, concurrency=%d",
                 self.title, len(self.tasks), self.concurrency)

        while True:
            self._propagate_skips()
            ready = [t for t in list(self.tasks.values())
                     if t.state == PENDING and self._ready(t)]
            for t in ready:
                t.state = RUNNING
                running.add(asyncio.create_task(self._run_task(t)))

            if not running:
                break

            done, running = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                if d.exception() is not None:        # _run_task should never raise
                    raise d.exception()

            if stop_when is not None and stop_when(state):
                stopped = True
            if stopped:
                log.info("flow %r: stop_when met — cancelling %d in-flight (%d done)",
                         self.title, len(running), state.done)
                for r in running:
                    r.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                break

        if self.runs_dir is not None:
            for node in {t.node for t in self.tasks.values()}:
                self._flush_node(node)
        log.info("flow %r done in %.1fs — %d ok, %d failed, %d skipped",
                 self.title, time.time() - self._start,
                 state.done, state.failed, state.skipped)
        return state

    def _ready(self, t):
        states = [self.tasks[d].state for d in t.deps]
        if t.gather:
            return all(s in _TERMINAL for s in states)
        return all(s == DONE for s in states)

    def _propagate_skips(self):
        changed = True
        while changed:
            changed = False
            for t in self.tasks.values():
                if t.state != PENDING or t.gather:
                    continue
                dead = [d for d in t.deps
                        if self.tasks[d].state in (FAILED, SKIPPED)]
                if dead:
                    t.state = SKIPPED
                    changed = True
                    log.debug("∅ %s skipped (upstream %s)", t.id, dead[0])

    async def _run_task(self, t):
        async with self._acquire(t):
            path = None
            if self.runs_dir is not None:
                leaf = t.id.split("/")[-1]
                path = self.runs_dir / t.node / f"{leaf}.progress.json"
            log.debug("→ %s", t.id)
            t0 = time.time()
            try:
                if path is not None:
                    with _monitor(t.id, 1, path, parent=t.node,
                                  meta={"node": t.node, "deps": list(t.deps)},
                                  min_interval=0) as m:
                        tok = _task_monitor.set(m)
                        try:
                            r = await t.run(self.results)
                        finally:
                            _task_monitor.reset(tok)
                        m.update()
                else:
                    r = await t.run(self.results)
                t.result = r
                t.state = DONE
                self.results[t.id] = r
                log.debug("✓ %s (%.2fs)", t.id, time.time() - t0)
                for cb in self._on_done.get(t.id, ()):   # dynamic fan-out hooks
                    cb(r)
            except _Filtered as f:
                t.state = FAILED
                t.error = f
                log.debug("⊘ %s pruned: %s", t.id, "; ".join(map(str, f.issues)))
                if path is not None:
                    mark(path, state="failed",
                         extra={"error": "filtered: " + "; ".join(map(str, f.issues))})
            except Exception as e:                       # captured; never aborts run
                t.state = FAILED
                t.error = e
                log.warning("✗ %s failed: %r", t.id, e)
            finally:
                if self.runs_dir is not None:
                    self._flush_node(t.node)

    @asynccontextmanager
    async def _acquire(self, t):
        async with self._gsem:
            ns = self._node_sems.get(t.node)
            if ns is not None:
                async with ns:
                    yield
            else:
                yield

    def _flush_node(self, node):
        tasks = [t for t in self.tasks.values()
                 if t.node == node and t.state != SKIPPED and t.run is not None]
        if not tasks:
            return
        done = sum(1 for t in tasks if t.state == DONE)
        failed = sum(1 for t in tasks if t.state == FAILED)
        node_state = DONE if all(t.state in _TERMINAL for t in tasks) else RUNNING
        data = {"name": node, "parent": None, "total": len(tasks), "done": done,
                "state": node_state, "started": self._start, "ended": None,
                "extra": ({"failed": failed} if failed else {}), "meta": {}}
        p = self.runs_dir / node / "_node.progress.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))

    def _flush_graph(self):
        """Persist the node-level topology (kinds + edges + topo rank) to
        ``runs_dir/graph.json`` so the dashboard can draw the actual DAG — its
        shape, direction, and per-node kind — not just a flat list of progress
        files. Written once at run start; the topology is fixed by then."""
        nodes = set(self._node_kind) | {t.node for t in self.tasks.values()}
        adj = {n: set() for n in nodes}
        indeg = {n: 0 for n in nodes}
        for a, b in self._edges:
            if a in nodes and b in nodes and b not in adj[a]:
                adj[a].add(b)
                indeg[b] += 1
        rank = {n: 0 for n in nodes}                  # longest-path layering
        left = dict(indeg)
        q = deque(n for n in nodes if indeg[n] == 0)
        while q:
            n = q.popleft()
            for m in adj[n]:
                rank[m] = max(rank[m], rank[n] + 1)
                left[m] -= 1
                if left[m] == 0:
                    q.append(m)
        data = {"title": self.title,
                "nodes": [{"name": n, "kind": self._node_kind.get(n, "task"),
                           "rank": rank[n]}
                          for n in sorted(nodes, key=lambda x: (rank[x], x))],
                "edges": sorted(list(e) for e in self._edges)}
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / "graph.json").write_text(json.dumps(data))


# --- calling convention for a unit-fn -------------------------------------- #
# Nodes call a unit-fn as `fn(item)`; the combinators below give it more context
# (which attempt, what feedback) by passing `attempt=`/`feedback=` *only if* the
# fn accepts them — so a plain `fn(item)` keeps working while an opt-in
# `async def fn(item, *, attempt=0, feedback=None)` gets the extra context.
async def _call(fn, item, **context):
    """Await ``fn(item, **kw)`` with the subset of `context` that `fn` accepts."""
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return await fn(item, **context)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kw = context
    else:
        kw = {k: v for k, v in context.items() if k in params}
    return await fn(item, **kw)


def _handles_in(obj):
    """Yield every `Handle` reachable in `obj` (scalars, lists/tuples, dict values)."""
    if isinstance(obj, Handle):
        yield obj
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from _handles_in(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _handles_in(x)


def _resolve(obj, results):
    """Substitute every `Handle` in `obj` with its result(s) — a one-task handle
    becomes its scalar result, a collection handle becomes the list of survivors."""
    if isinstance(obj, Handle):
        if obj.kind == "one":
            return results.get(obj.ids[0]) if obj.ids else None
        return [results[i] for i in obj.ids if i in results]
    if isinstance(obj, list):
        # a list of handles is an aggregation: drop ones that didn't produce a
        # result (skipped/failed), so you reduce over the survivors
        out = []
        for x in obj:
            if isinstance(x, Handle) and x.kind == "one":
                if x.ids and x.ids[0] in results:
                    out.append(results[x.ids[0]])
            else:
                out.append(_resolve(x, results))
        return out
    if isinstance(obj, tuple):
        return tuple(_resolve(x, results) for x in obj)
    if isinstance(obj, dict):
        return {k: _resolve(v, results) for k, v in obj.items()}
    return obj


def _apply_pred(pred, item):
    res = pred(item)
    ok, issues = res if isinstance(res, tuple) else (bool(res), [])
    if not ok:
        raise _Filtered(issues)
    return item


# --- type introspection for flow.check() ----------------------------------- #
def _hints(fn):
    """Resolved type hints for `fn`, or {} if they can't be resolved (forgiving)."""
    try:
        return get_type_hints(fn)
    except Exception:
        return getattr(fn, "__annotations__", {}) or {}


def _ret(fn):
    """`fn`'s return type (Any if unannotated/unresolvable)."""
    return _hints(fn).get("return", Any)


def _param0(fn):
    """The annotation of `fn`'s first positional parameter (Any if none/unknown)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return Any
    hints = _hints(fn)
    for p in params:
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            return hints.get(p.name, Any)
    return Any


def _elem_of(annotation):
    """The element type of an `Iterable[E]` / `list[E]` annotation (Any otherwise)."""
    args = get_args(annotation)
    return args[0] if args else Any


def _is_any(t):
    return t is Any or t is None or t is inspect.Parameter.empty


def _members(t):
    """Flatten a Union/Optional into its member types; otherwise [t]."""
    if get_origin(t) is Union:
        return list(get_args(t))
    return [t]


def _compatible(provided, expected):
    """Is a value of type `provided` acceptable where `expected` is wanted?

    Deliberately forgiving — a linter, not a type checker: unknown/`Any` matches
    anything, and anything it can't reason about passes. It exists to catch the
    clear mistakes (a `str` wired into a step that wants a `Model`).
    """
    if _is_any(provided) or _is_any(expected):
        return True
    # provided is OK if every provided member fits some expected member
    return all(any(_match_one(p, e) for e in _members(expected))
               for p in _members(provided))


def _match_one(p, e):
    if _is_any(p) or _is_any(e):
        return True
    op, oe = get_origin(p), get_origin(e)
    if oe is not None:                       # expected is a generic (e.g. list[X])
        if op is None:                       # provided is a bare class
            return _subclass(p, oe)
        if not (op is oe or _subclass(op, oe)):
            return False
        ap, ae = get_args(p), get_args(e)
        if ap and ae:
            return all(_match_one(x, y) for x, y in zip(ap, ae))
        return True
    if op is not None:                       # provided generic vs plain expected class
        return _subclass(op, e)
    return p is e or _subclass(p, e)


def _subclass(a, b):
    try:
        return issubclass(a, b)
    except TypeError:
        return False


def _tn(t):
    """A short readable name for a type, for check() error messages."""
    if _is_any(t):
        return "Any"
    return getattr(t, "__name__", None) or str(t).replace("typing.", "")


async def _bounded_gather(thunks, concurrency):
    """Run zero-arg coroutine `thunks` at most `concurrency` at once, gathering
    results in order; an exception is returned in place of its result."""
    sem = asyncio.Semaphore(concurrency)
    async def run(thunk):
        async with sem:
            try:
                return await thunk()
            except Exception as e:
                return e
    return await asyncio.gather(*(run(t) for t in thunks))


# --- node fn-policies ------------------------------------------------------ #
def best_of(fn, n, *, judge=None, score=None, concurrency=None, monitor_path=None):
    """Wrap `fn` into a unit-fn that runs `n` attempts and returns the best one.

    A node `policy`: drop it into `map` to fan out every item into `n` independent
    attempts of `fn(item, attempt=i)` (use `attempt` to vary a seed; plain
    `fn(item)` works too) and keep the best. Pick the winner with exactly one of
    ``judge(results) -> int`` (async-or-sync, returns the winning index) or
    ``score(result) -> float`` (keep the max; ties to the earliest attempt).

    Attempts that raised are dropped; if *all* raise, the first exception is
    returned. With `monitor_path` (``result -> path | None``) the losing attempts
    are marked `failed` ("best_of: not selected") on the dashboard.

        flow.map("solve", units, best_of(solve, n=4, score=reward))
    """
    if (judge is None) == (score is None):
        raise ValueError("best_of needs exactly one of judge= or score=")

    async def best_of_unit(unit, **context):
        results = await _bounded_gather(
            [lambda i=i: _call(fn, unit, **{**context, "attempt": i})
             for i in range(n)],
            concurrency or n)
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

    A node `policy`: each item runs `fn(item)`; if the result fails `check` — or
    `fn` raises — it re-runs as `fn(item, attempt=i, feedback=fb)` feeding the
    previous try's feedback back in, until it passes or `max_attempts` is hit.
    `check(result) -> (ok, issues)` is a gate predicate; `feedback` defaults to the
    `issues` list, or pass ``feedback(result, issues) -> any`` to transform it.

    Returns the first passing result, else the last failing result (or exception).
    Superseded attempts are marked `failed` on the dashboard if `monitor_path` is
    given. Retries are sequential per item; fan items out across the node.

        flow.map("solve", units, with_retry(solve, check=parses))
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
