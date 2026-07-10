"""Unit tests for content-keyed step memoization — replay on identical re-run,
invalidation on changed fn / inputs, refresh semantics, failure never cached,
cache=False opt-out, downstream flow of cached values, and the spawn surface."""
import asyncio
import json

from stagehand import Flow, Memo, fn_fingerprint, memo_key


def _run(f, **kw):
    return asyncio.run(f.run(**kw))


def _counted(calls, fn):
    """Wrap `fn` counting invocations (proxy for 'did the expensive thing run')."""
    async def wrapper(x):
        calls.append(x)
        return fn(x)
    return wrapper


def _a(fn):
    """Lift a sync fn to the async calling convention `Flow.map` expects."""
    async def af(x):
        return fn(x)
    return af


# ---- store ------------------------------------------------------------------ #
def test_memo_store_round_trip(tmp_path):
    m = Memo(tmp_path / "memo")
    assert m.get("k") == (False, None)
    assert m.put("k", {"a": [1, 2]}, node="n") is True
    assert m.get("k") == (True, {"a": [1, 2]})
    assert len(m) == 1


def test_memo_store_rejects_unserializable(tmp_path):
    m = Memo(tmp_path / "memo")
    assert m.put("k", object()) is False       # skipped, not an error
    assert m.get("k") == (False, None)


# ---- keys ------------------------------------------------------------------- #
def test_key_changes_with_fn_and_inputs():
    k = memo_key("src-a", {"n": 1}, [10])
    assert memo_key("src-a", {"n": 1}, [10]) == k          # deterministic
    assert memo_key("src-B", {"n": 1}, [10]) != k          # fn changed
    assert memo_key("src-a", {"n": 2}, [10]) != k          # static changed
    assert memo_key("src-a", {"n": 1}, [11]) != k          # dep value changed


def test_fingerprint_recurses_into_closures():
    def inner_a(x):
        return x + 1
    def inner_b(x):
        return x + 2
    def wrap(g):
        def policy(x):
            return g(x)
        return policy
    assert fn_fingerprint(wrap(inner_a)) != fn_fingerprint(wrap(inner_b))


# ---- engine: replay & invalidation ------------------------------------------ #
def test_identical_rerun_is_free(tmp_path):
    calls = []
    def build():
        f = Flow(memo=tmp_path / "memo")
        f.map("double", [1, 2, 3], _counted(calls, lambda x: x * 2))
        return f
    out1 = build()
    _run(out1)
    assert sorted(calls) == [1, 2, 3]
    out2 = build()
    st = _run(out2)
    assert sorted(calls) == [1, 2, 3]          # nothing re-ran
    assert st.done == 3                        # cached tasks still count done
    assert sorted(v for t, v in out2.results.items()) == [2, 4, 6]


def test_changed_input_reruns_only_that_item(tmp_path):
    calls = []
    def build(items):
        f = Flow(memo=tmp_path / "memo")
        f.map("double", items, _counted(calls, lambda x: x * 2))
        return f
    _run(build([1, 2]))
    _run(build([1, 5]))
    assert calls == [1, 2, 5]                  # 1 replayed, 5 ran


def test_downstream_reruns_when_upstream_value_changes(tmp_path):
    seen = []
    def build(items):
        f = Flow(memo=tmp_path / "memo")
        up = f.map("up", items, _a(lambda x: x * 10))
        f.map("down", up, _counted(seen, lambda v: v + 1))
        return f
    _run(build([1]))
    _run(build([1]))                           # both layers replay
    assert seen == [10]
    _run(build([2]))                           # upstream value changed ⇒ down runs
    assert seen == [10, 20]


def test_refresh_reruns_and_rerecords(tmp_path):
    calls = []
    def build():
        f = Flow(memo=tmp_path / "memo")
        f.map("double", [7], _counted(calls, lambda x: x * 2))
        return f
    _run(build())
    _run(build(), refresh=True)                # deliberately new run
    assert calls == [7, 7]
    _run(build())                              # refreshed record replays again
    assert calls == [7, 7]


def test_failure_is_never_cached(tmp_path):
    attempts = []
    def flaky(x):
        attempts.append(x)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return x
    def build():
        f = Flow(memo=tmp_path / "memo")
        f.map("step", [1], _a(flaky))
        return f
    st1 = _run(build())
    assert st1.failed == 1
    st2 = _run(build())                        # failure did not poison the cache
    assert st2.done == 1
    assert attempts == [1, 1]


def test_cache_false_node_always_runs(tmp_path):
    calls = []
    def build():
        f = Flow(memo=tmp_path / "memo")
        f.map("effect", [1], _counted(calls, lambda x: x), cache=False)
        return f
    _run(build())
    _run(build())
    assert calls == [1, 1]


def test_no_memo_store_means_no_caching(tmp_path):
    calls = []
    def build():
        f = Flow()                             # no memo ⇒ vanilla behavior
        f.map("double", [1], _counted(calls, lambda x: x * 2))
        return f
    _run(build())
    _run(build())
    assert calls == [1, 1]


def test_unserializable_result_runs_every_time(tmp_path):
    calls = []
    def build():
        f = Flow(memo=tmp_path / "memo")
        f.map("obj", [1], _counted(calls, lambda x: object()))
        return f
    _run(build())
    _run(build())
    assert calls == [1, 1]                     # can't record ⇒ run-always


def test_cached_hit_feeds_expand_fanout(tmp_path):
    """A cache hit still fires dynamic fan-out hooks downstream."""
    def build():
        f = Flow(memo=tmp_path / "memo")
        up = f.map("gen", [3], _a(lambda n: list(range(n))))
        items = f.expand("split", up, lambda xs: xs)
        return f, f.map("inc", items, _a(lambda x: x + 1))
    f1, out1 = build()
    _run(f1)
    assert sorted(out1.results()) == [1, 2, 3]
    f2, out2 = build()
    _run(f2)                                   # gen replays from cache
    assert sorted(out2.results()) == [1, 2, 3]


# ---- spawn surface ----------------------------------------------------------- #
def test_spawn_memo_replays(tmp_path):
    calls = []
    def step(x):
        calls.append(x)
        return x * 2
    async def main():
        f = Flow(memo=tmp_path / "memo")
        f.spawn(step, (21,))
        await f.run()
    asyncio.run(main())
    asyncio.run(main())
    assert calls == [21]                       # replayed via spawn too


def test_memo_entry_records_provenance(tmp_path):
    f = Flow(memo=tmp_path / "memo")
    f.map("double", [1], _a(lambda x: x * 2))
    _run(f)
    entry = json.loads(next((tmp_path / "memo").glob("*.json")).read_text())
    assert entry["result"] == 2
    assert entry["node"] == "double"
    assert "ts" in entry
