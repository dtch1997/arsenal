"""Unit tests for smoke mode — fan-out truncation across map/filter/reduce/
expand/each, full-DAG traversal (analysis steps still run), memo-cache
segregation from real runs, and the manifest tag."""
import asyncio
import json

from stagehand import Flow
from stagehand import flow, each, run


def _run(f, **kw):
    return asyncio.run(f.run(**kw))


def _a(fn):
    async def af(x):
        return fn(x)
    return af


def test_map_and_reduce_truncate():
    f = Flow(smoke=2)
    doubled = f.map("double", range(100), _a(lambda x: x * 2))
    total = f.reduce("sum", doubled, sum)
    _run(f)
    assert len(doubled.results()) == 2
    assert total.result == 0 + 2               # items 0,1 → 0+2


def test_filter_truncates():
    f = Flow(smoke=3)
    kept = f.filter("gate", range(50), lambda x: True)   # preds are sync-called
    _run(f)
    assert len(kept.results()) == 3


def test_expand_truncates_each_fanout():
    f = Flow(smoke=2)
    up = f.map("gen", [10], _a(lambda n: list(range(n))))
    items = f.expand("split", up, lambda xs: xs)
    inc = f.map("inc", items, _a(lambda x: x + 1))
    _run(f)
    assert sorted(inc.results()) == [1, 2]     # 10 elements → 2


def test_whole_dag_still_runs():
    """Smoke exercises every node — including the analysis tail — not a prefix."""
    ran = []
    f = Flow(smoke=1)
    up = f.map("sample", [1, 2, 3], _a(lambda x: x))
    agg = f.reduce("analyze", up, lambda vals: (ran.append("analyze"), len(vals))[1])
    _run(f)
    assert ran == ["analyze"]                  # analysis step executed
    assert agg.result == 1


def test_smoke_none_changes_nothing():
    f = Flow()
    out = f.map("double", range(5), _a(lambda x: x * 2))
    _run(f)
    assert len(out.results()) == 5


def test_smoke_memo_segregated_from_real(tmp_path):
    """A smoke run's cached results must not replay into a real run."""
    calls = []
    def build(smoke):
        f = Flow(memo=tmp_path / "memo", smoke=smoke)
        async def step(x):
            calls.append((smoke, x))
            return x * 2
        f.map("double", [1, 2], step)
        return f
    _run(build(1))                             # smoke: runs item 1 only
    assert calls == [(1, 1)]
    _run(build(None))                          # real: both items RUN (no smoke hits)
    assert calls == [(1, 1), (None, 1), (None, 2)]
    _run(build(1))                             # smoke again: replays smoke cache
    _run(build(None))                          # real again: replays real cache
    assert calls == [(1, 1), (None, 1), (None, 2)]


def test_manifest_records_smoke(tmp_path):
    f = Flow(runs_dir=tmp_path / "runs", smoke=2)
    f.map("double", [1, 2, 3], _a(lambda x: x * 2))
    _run(f)
    m = json.loads((tmp_path / "runs" / "manifest.json").read_text())
    assert m["flow"]["smoke"] == 2
    assert m["flow"]["tasks"] == 2             # truncated task count


def test_dsl_each_truncates(tmp_path):
    seen = []
    def step(x):
        seen.append(x)
        return x
    async def main():
        with flow(smoke=2):
            each(step, [10, 20, 30, 40])
            await run()
    asyncio.run(main())
    assert sorted(seen) == [10, 20]
