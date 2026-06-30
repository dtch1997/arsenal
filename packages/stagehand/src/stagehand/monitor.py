"""monitor — a tiny file-backed monitoring context for a unit of work.

It does two things: (1) tracks progress (a `done`/`total` ticker) and (2) watches the
unit's state — `running` -> `done`, or `failed` (capturing the exception) if it errors
out. Any unit of work writes its state to a JSON file that a dashboard (or any other
process) can poll. Monitors form a DAG via ``parent`` (a sweep monitor is the parent
of per-cell train/eval monitors), so a dashboard renders a tree just by reading files.

    with monitor("ed_neg_sft_s0", total=256,
                 path="runs/ed_neg_sft_s0/train.progress.json", parent="sweep") as m:
        for batch in batches:
            ...
            m.update(loss=loss)        # advance the ticker, record extra fields

On clean exit the file's state -> "done"; on exception -> "failed" with the error
recorded, then the exception propagates (so the caller still sees it). Writes are
throttled to `min_interval` seconds, but start / finish / failure always flush.

Pass ``cleanup=True`` for an *ephemeral* monitor: the progress file is removed
when the context exits (success or failure) instead of being left at its final
state. Use this when the unit's outcome is recorded elsewhere and the
``progress.json`` is only needed to show live progress while it runs (e.g. a
driver that keeps its own persistent state and just wants a live ticker).
"""
from __future__ import annotations
import json, time
from contextlib import contextmanager
from pathlib import Path

SUFFIX = ".progress.json"


class Monitor:
    def __init__(self, state, flush):
        self._state = state
        self._flush = flush

    def update(self, n=1, **extra):
        """Advance the ticker by `n` and record/overwrite `extra` fields (e.g. loss)."""
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
def monitor(name, total, path, *, parent=None, meta=None, min_interval=0.5,
            cleanup=False):
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
    try:
        yield Monitor(state, flush)
        state["state"] = "done"
    except BaseException as e:           # erred out: record it, then re-raise
        state["state"] = "failed"
        state["extra"]["error"] = repr(e)
        raise
    finally:
        state["ended"] = time.time()
        if cleanup:
            p.unlink(missing_ok=True)    # ephemeral: drop the live-progress file
        else:
            flush(force=True)


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
