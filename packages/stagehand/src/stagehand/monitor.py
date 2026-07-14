"""monitor — a tiny file-backed progress ticker for a *loop* of work.

The ticker exists to make inner loops visible: training steps, eval items,
rollout batches. A monitor tracks `done`/`total` progress plus lifecycle state
(`running` -> `done`, or `failed` with the exception captured), written to a
JSON file that a dashboard (or any other process) can poll. Monitors form a
tree via ``parent``, so a dashboard renders nested progress just by reading
files.

The headline API is `track` — tqdm-shaped, it ticks once per iteration:

    from stagehand import track

    t = track(batches, "train")            # total inferred from len(batches)
    for batch in t:
        loss = step(batch)
        t.set(loss=loss)                   # ride-along fields, flushed with the tick

`monitor` is the underlying context manager, for loops that don't fit an
iterable (`m.update(loss=...)` per step advances the ticker by hand).

**What a monitor is NOT for: step-level status.** Inside a flow, the engine
already writes a running/done/failed monitor per task — wrapping a whole step
in ``monitor(..., total=1)`` and calling ``update(n=1)`` once at the end
duplicates that and shows no progress. If you're holding a monitor whose
`total` is 1, you almost certainly want either nothing (the engine has it
covered) or `current_monitor().set(**fields)` to attach fields to the task's
existing monitor.

**Nesting is automatic.** Opening a monitor (or `track`) while another is open
— e.g. inside an engine step — parents it to the enclosing one and drops its
file alongside, so `path=`/`parent=` are rarely needed. Across a *process*
boundary, pass the linkage explicitly:

    subprocess.run(argv, env={**os.environ, **monitor_env()})   # parent step

and the child's bare `track(batches, "train")` nests under the parent task on
the dashboard. This is the intended shape for training scripts run as
subprocesses — instrument the loop in the child, hand it `monitor_env()` from
the step.

While running, the file's state is "running"; writes are throttled to
`min_interval` seconds, but start / finish / failure always flush. Monitors
are **ephemeral by default** (``cleanup=True``): the progress file is removed
when the context exits (success *or* failure), since the common case is a live
ticker whose outcome is recorded elsewhere. Pass ``cleanup=False`` to persist
the final state instead. On exception the error is recorded and re-raised
regardless of ``cleanup``.
"""
from __future__ import annotations
import json, os, time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ._log import log

SUFFIX = ".progress.json"
ENV_DIR = "STAGEHAND_MONITOR_DIR"        # dir for env-linked child monitors
ENV_PARENT = "STAGEHAND_MONITOR_PARENT"  # parent name for env-linked children

# The innermost open monitor on this context — lets nested monitors/tracks
# auto-parent, and lets a step stream fields to its task's monitor without
# threading the handle through every fn. The engine opens one per task.
_current: ContextVar = ContextVar("stagehand_monitor", default=None)


def current_monitor():
    """The innermost open `Monitor` (or None). Inside an engine step this is
    the task's monitor — `current_monitor().set(**fields)` pushes live fields
    to the dashboard; a `monitor`/`track` opened here auto-parents to it."""
    return _current.get()


def monitor_env() -> dict:
    """Env vars that link a subprocess's monitors under the current one.

    Hand these to any child process that runs a loop worth watching (training
    scripts especially)::

        subprocess.run(argv, env={**os.environ, **monitor_env()})

    In the child, a bare ``monitor("train", total=n)`` / ``track(batches,
    "train")`` then writes next to the parent's progress file and nests under
    it on the dashboard — no explicit ``path=``/``parent=``. Inside an already
    linked subprocess (no open monitor) the linkage is passed through
    unchanged, so grandchildren keep nesting."""
    m = current_monitor()
    if m is not None and m.path is not None:
        return {ENV_DIR: str(m.path.parent), ENV_PARENT: m.name}
    return {k: os.environ[k] for k in (ENV_DIR, ENV_PARENT) if k in os.environ}


def _resolve(name, path, parent):
    """Fill in path/parent: explicit -> enclosing monitor -> env link -> cwd."""
    enclosing = current_monitor()
    if parent is None:
        parent = (enclosing.name if enclosing is not None
                  else os.environ.get(ENV_PARENT))
    if path is None:
        if enclosing is not None and enclosing.path is not None:
            d = enclosing.path.parent
        elif ENV_DIR in os.environ:
            d = Path(os.environ[ENV_DIR])
        else:
            d = Path(".")
        path = d / f"{name}{SUFFIX}"
    return Path(path), parent


class Monitor:
    def __init__(self, state, flush, path=None):
        self._state = state
        self._flush = flush
        self.path = Path(path) if path is not None else None

    @property
    def name(self):
        return self._state["name"]

    def update(self, n=1, **extra):
        """Advance the ticker by `n` and record/overwrite `extra` fields (e.g.
        loss). This is the once-per-iteration call of a loop — `track` wraps an
        iterable and makes it automatic. Not a completion flag: a single
        `update(n=1)` at the end of a step shows no progress (and the engine
        already tracks step state)."""
        self._state["done"] += n
        if extra:
            self._state["extra"].update(extra)
        self._flush()

    def set(self, **extra):
        """Record fields without advancing the ticker (forces a write)."""
        self._state["extra"].update(extra)
        self._flush(force=True)

    @property
    def state(self):
        return self._state


@contextmanager
def monitor(name, total=None, path=None, *, parent=None, meta=None,
            min_interval=0.5, cleanup=True):
    """Open a progress ticker for a loop of `total` iterations.

    `path` and `parent` are usually inferred — from the enclosing monitor
    (engine task or outer `monitor`), else from `monitor_env()` vars in a
    linked subprocess, else the cwd. `total=None` means indeterminate."""
    path, parent = _resolve(name, path, parent)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = {"name": name, "parent": parent, "total": total, "done": 0,
             "state": "running", "started": time.time(), "ended": None,
             "extra": {}, "meta": meta or {}}
    last = [0.0]

    def flush(force=False):
        if force or time.time() - last[0] >= min_interval:
            p.write_text(json.dumps(state))
            last[0] = time.time()

    flush(force=True)
    m = Monitor(state, flush, path=p)
    tok = _current.set(m)
    try:
        yield m
        state["state"] = "done"
    except BaseException as e:           # erred out: record it, then re-raise
        state["state"] = "failed"
        state["extra"].setdefault("error", repr(e))   # keep a more specific note
        raise
    finally:
        _current.reset(tok)
        state["ended"] = time.time()
        if total == 1 and "status" in state["extra"]:
            log.warning(
                "monitor %r looks like a status flag (total=1 + a 'status' "
                "field): step state is tracked automatically — the ticker is "
                "for loop progress; see stagehand.track", name)
        if cleanup:
            p.unlink(missing_ok=True)    # ephemeral: drop the live-progress file
        else:
            flush(force=True)


class Tracker:
    """`track`'s handle: iterate to tick, `set(**fields)` for ride-along data.

    Single-use — iterating opens the monitor (one tick per item, state `done`
    on exhaustion, `failed` if the loop body raises) and `set` records fields
    that flush with the next tick's throttled write."""

    def __init__(self, iterable, name, *, total=None, path=None, parent=None,
                 meta=None, min_interval=0.5, cleanup=True):
        if total is None:
            try:
                total = len(iterable)
            except TypeError:
                pass                      # indeterminate: total stays None
        self._it = iterable
        self._name = name
        self._kw = dict(total=total, path=path, parent=parent, meta=meta,
                        min_interval=min_interval, cleanup=cleanup)
        self._m = None
        self._pending = {}

    def __iter__(self):
        with monitor(self._name, **self._kw) as m:
            self._m = m
            if self._pending:
                m.state["extra"].update(self._pending)
                self._pending.clear()
            try:
                for x in self._it:
                    yield x
                    m.update()
            except GeneratorExit:
                # the caller raised or broke out mid-loop; the real exception
                # (if any) is theirs — all we know is the loop didn't finish
                m.state["extra"]["error"] = "stopped early (break or exception in loop body)"
                raise

    def set(self, **fields):
        """Record fields (e.g. loss) to ride along with the next tick — no
        forced write, so calling it every iteration stays cheap."""
        if self._m is None:
            self._pending.update(fields)
        else:
            self._m.state["extra"].update(fields)


def track(iterable, name, *, total=None, path=None, parent=None, meta=None,
          min_interval=0.5, cleanup=True) -> Tracker:
    """Monitor a loop by wrapping its iterable (tqdm-shaped)::

        t = track(batches, "train")        # total from len(batches)
        for batch in t:
            loss = step(batch)
            t.set(loss=loss)

    Ticks once per item; `total` is inferred from ``len(iterable)`` when
    possible. Nests under the enclosing monitor (or the `monitor_env()` link
    in a subprocess) exactly like `monitor`."""
    return Tracker(iterable, name, total=total, path=path, parent=parent,
                   meta=meta, min_interval=min_interval, cleanup=cleanup)


def mark(path, *, extra=None, **fields):
    """Post-hoc patch a monitor file (e.g. a unit that passed but failed a later gate).
    No-op if the file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return
    state = json.loads(p.read_text())
    state.update(fields)
    if extra:
        state.setdefault("extra", {}).update(extra)
    p.write_text(json.dumps(state))


def read_monitors(root):
    """Load every ``*.progress.json`` under `root` (recursively)."""
    out = []
    for p in sorted(Path(root).glob(f"**/*{SUFFIX}")):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            pass  # mid-write; the next poll picks it up
    return out


def read_graph(root):
    """Load the node-level topology the engine writes to ``root/graph.json``
    (``{title, nodes:[{name,kind,rank}], edges:[[src,dst]]}``), or None if it
    isn't there yet — the dashboard falls back to a flat table in that case."""
    p = Path(root) / "graph.json"
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
