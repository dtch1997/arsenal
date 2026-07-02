"""memo — content-keyed step memoization: re-running a `Flow` is free.

Give a flow a memo store and every completed task's result is persisted under a
key derived from **everything that could change the answer**: the step fn's
source (recursing into closure cells, so `best_of`/`with_retry` wrappers include
the wrapped fn), the static inputs it was declared with, and the *values* of its
dependencies' results. Same code + same inputs ⇒ the task is served from the
store without running; change the fn or an upstream value and it (plus its
downstream) re-runs. A crashed 200-task sweep resumes where it died.

    flow = Flow("runs", memo="runs/memo")
    ...
    await flow.run()                 # first time: everything runs
    await flow.run()                 # second time: everything replays, zero work
    await flow.run(refresh=True)     # deliberately a NEW experiment: re-run + re-record

This is exactly the honest semantics for nondeterministic (LLM-sampling) steps:
a re-run **replays the persisted samples** — the artifact is the experiment —
and `refresh=True` is the explicit "generate fresh samples" act. Mark a node
`cache=False` to exempt it (e.g. a step with side effects you always want).

Ground rules:

  - Only *successful* results are recorded — failures and filtered items always
    re-run.
  - Results must round-trip through JSON to be cached (tuples come back as
    lists); a non-serializable result is silently run-always, never corrupted.
  - Keys hash non-JSON input values via `repr`. A repr with a memory address is
    unstable across processes, which degrades to a cache *miss* — the failure
    direction is always "did the work again", never "wrong result". Passing
    `Artifact`s between steps gives stable, content-addressed keys for free.
  - The store is a plain directory of `<key>.json` files (atomic writes) — on a
    shared filesystem it's shared across processes; delete the directory to
    drop the cache.
"""
from __future__ import annotations
import hashlib
import inspect
import json
import os
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path


class Memo:
    """A key → JSON-result store backed by a local directory (`<key>.json`)."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, key):
        """`(hit, result)` — `(False, None)` on absent or unreadable entries."""
        p = self.root / f"{key}.json"
        try:
            return True, json.loads(p.read_text())["result"]
        except (OSError, json.JSONDecodeError, KeyError):
            return False, None

    def put(self, key, result, **meta) -> bool:
        """Record `result` (plus provenance `meta`) under `key`; returns False —
        without failing the task — when the result isn't JSON-serializable."""
        try:
            body = json.dumps({"result": result,
                               "ts": datetime.now(timezone.utc)
                                             .isoformat(timespec="seconds"),
                               **meta}, sort_keys=True)
        except (TypeError, ValueError):
            return False
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, self.root / f"{key}.json")   # atomic: never a torn read
        return True

    def __len__(self):
        return sum(1 for _ in self.root.glob("*.json"))


def fn_fingerprint(fn, _depth=0) -> str:
    """A source-level identity for a step fn. Recurses (bounded) into closure
    cells so a policy wrapper's fingerprint includes the user fn it wraps —
    editing the wrapped fn invalidates the cache even through `best_of`. Falls
    back to the qualname for builtins/C fns (their "source" never changes)."""
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        src = getattr(fn, "__qualname__", repr(type(fn)))
    parts = [src]
    if _depth < 3:
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                v = cell.cell_contents
            except ValueError:                       # empty cell
                continue
            if callable(v):
                parts.append(fn_fingerprint(v, _depth + 1))
    return "\n".join(parts)


def memo_key(fingerprint: str, static, dep_vals) -> str:
    """Hash of everything that could change the answer: fn source + declared
    static inputs + upstream result values. Non-JSON values hash via `repr`."""
    payload = json.dumps({"fn": fingerprint, "static": static, "deps": dep_vals},
                         sort_keys=True, default=repr)
    return hashlib.md5(payload.encode()).hexdigest()
