"""Unit tests for the DAG engine (Flow) and the node fn-policies (best_of /
with_retry). Async tests drive their own loop via asyncio.run so there's no
pytest-asyncio dependency."""
import asyncio
import json

from stagehand import Flow, best_of, with_retry
from stagehand.monitor import read_monitors, read_graph


async def ident(x):
    return x


# ---- map ------------------------------------------------------------------ #
def test_map_static_source():
    async def tenx(x):
        return x * 10
    flow = Flow(concurrency=4)
    out = flow.map("w", [0, 1, 2, 3], tenx)
    asyncio.run(flow.run())
    assert sorted(out.results()) == [0, 10, 20, 30]


def test_map_streams_without_a_barrier():
    order = []

    async def train(i):
        await asyncio.sleep(0.02 * (i + 1))     # later units finish later
        order.append(("train", i))
        return i

    async def eval_(i):
        order.append(("eval", i))
        await asyncio.sleep(0.005)
        return i * 10

    flow = Flow(concurrency=8)
    trained = flow.map("train", range(4), train)
    evals = flow.map("eval", trained, eval_)
    asyncio.run(flow.run())

    kinds = [n for n, _ in order]
    # an eval ran before the last train finished -> the stages are NOT barriered
    assert kinds.index("eval") < len(kinds) - 1 - kinds[::-1].index("train")
    assert sorted(evals.results()) == [0, 10, 20, 30]


def test_map_concurrency_cap():
    live = peak = 0

    async def fn(_i):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return None

    flow = Flow(concurrency=3)
    flow.map("w", range(10), fn)
    asyncio.run(flow.run())
    assert peak <= 3


def test_per_node_concurrency_cap():
    live = peak = 0

    async def fn(_i):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return None

    flow = Flow(concurrency=10)
    flow.map("w", range(8), fn, concurrency=2)
    asyncio.run(flow.run())
    assert peak <= 2


# ---- filter / reduce ------------------------------------------------------ #
def test_filter_prunes_and_skips_dependents():
    async def tenx(x):
        return x * 10
    flow = Flow(concurrency=4)
    src = flow.map("src", [1, 2, 3, 4], ident)
    keep = flow.filter("keep", src, lambda x: x % 2 == 0)      # evens survive
    out = flow.map("out", keep, tenx)
    state = asyncio.run(flow.run())
    assert sorted(out.results()) == [20, 40]
    assert state.failed == 2          # the two pruned filter tasks
    assert state.skipped == 2         # their downstream out tasks


def test_filter_predicate_with_issues_tuple():
    flow = Flow()
    src = flow.map("src", [1, 5, 9], ident)
    keep = flow.filter("keep", src, lambda x: (x >= 5, [f"{x}<5"]))
    asyncio.run(flow.run())
    assert sorted(keep.results()) == [5, 9]


def test_reduce_barriers_over_survivors():
    flow = Flow()
    src = flow.map("src", [1, 2, 3, 4], ident)
    keep = flow.filter("keep", src, lambda x: x != 3)
    total = flow.reduce("sum", keep, lambda xs: sum(xs))
    asyncio.run(flow.run())
    assert total.results() == [1 + 2 + 4]


def test_reduce_runs_after_all_deps_terminal():
    seen_at = {}
    n_done = [0]

    async def fn(i):
        await asyncio.sleep(0.01 * (i + 1))
        n_done[0] += 1
        return i

    def collect(xs):
        seen_at["n_done_when_reduce_ran"] = n_done[0]
        return sum(xs)

    flow = Flow(concurrency=8)
    src = flow.map("src", range(4), fn)
    flow.reduce("sum", src, collect)
    asyncio.run(flow.run())
    assert seen_at["n_done_when_reduce_ran"] == 4      # waited for the whole stage


# ---- expand (dynamic fan-out) --------------------------------------------- #
def test_expand_dynamic_fanout_then_map():
    flow = Flow()
    seeds = flow.map("seed", [1, 2, 3], ident)
    # seed n expands into n children
    items = flow.expand("items", seeds, lambda n: [f"{n}.{j}" for j in range(n)])
    out = flow.map("use", items, ident)
    asyncio.run(flow.run())
    assert sorted(out.results()) == ["1.0", "2.0", "2.1", "3.0", "3.1", "3.2"]


# ---- add (raw escape hatch) ----------------------------------------------- #
def test_add_raw_nodes_with_deps():
    flow = Flow()
    flow.add("a", lambda: 5)
    b = flow.add("b", lambda x: x + 1, deps=["a"])
    c = flow.add("c", lambda x: x * 2, deps=["b"])
    asyncio.run(flow.run())
    assert b.results() == [6] and c.results() == [12]


# ---- failure isolation ---------------------------------------------------- #
def test_failure_skips_dependents_not_the_whole_run():
    async def train(i):
        if i == 1:
            raise ValueError("boom")
        return i

    async def tenx(x):
        return x * 10

    flow = Flow()
    trained = flow.map("train", range(3), train)
    evals = flow.map("eval", trained, tenx)
    state = asyncio.run(flow.run())
    assert sorted(evals.results()) == [0, 20]      # eval of the failed train skipped
    assert state.failed == 1 and state.skipped == 1


# ---- stop_when ------------------------------------------------------------ #
def test_stop_when_halts_early():
    ran = []

    async def fn(i):
        await asyncio.sleep(0.01 * (i + 1))
        ran.append(i)
        return i

    flow = Flow(concurrency=2)
    flow.map("w", range(20), fn)
    state = asyncio.run(flow.run(stop_when=lambda s: s.done >= 3))
    assert state.done >= 3
    assert len(ran) < 20                            # did not run the whole graph


# ---- monitoring ----------------------------------------------------------- #
def test_writes_node_and_task_monitor_files(tmp_path):
    async def fn(i):
        return i
    flow = Flow(tmp_path, title="x")
    flow.map("train", range(2), fn)
    asyncio.run(flow.run())
    mons = {m["name"]: m for m in read_monitors(tmp_path)}
    assert "train/0" in mons and "train/1" in mons          # per-task
    node = mons["train"]                                     # node group
    assert node["state"] == "done" and node["total"] == 2 and node["done"] == 2


def test_writes_graph_topology_and_task_deps(tmp_path):
    async def fn(i):
        return i
    flow = Flow(tmp_path, title="x")
    trained = flow.map("train", range(2), fn)
    gated = flow.filter("gate", trained, lambda i: True)
    flow.reduce("pick", gated, lambda xs: xs)
    asyncio.run(flow.run())

    graph = read_graph(tmp_path)
    kinds = {n["name"]: n["kind"] for n in graph["nodes"]}
    ranks = {n["name"]: n["rank"] for n in graph["nodes"]}
    assert kinds == {"train": "map", "gate": "filter", "pick": "reduce"}
    assert ranks["train"] < ranks["gate"] < ranks["pick"]    # topo order
    assert ["train", "gate"] in graph["edges"] and ["gate", "pick"] in graph["edges"]

    # per-task deps are persisted so the dashboard can trace lineage
    mons = {m["name"]: m for m in read_monitors(tmp_path)}
    assert mons["gate/0"]["meta"]["deps"] == ["train/0"]


def test_filtered_task_marked_failed_on_dashboard(tmp_path):
    flow = Flow(tmp_path)
    src = flow.map("src", [1, 2], ident)
    flow.filter("keep", src, lambda x: x == 1)
    asyncio.run(flow.run())
    mons = {m["name"]: m for m in read_monitors(tmp_path)}
    pruned = [m for m in mons.values()
              if m["name"].startswith("keep/") and m["state"] == "failed"]
    assert len(pruned) == 1 and "filtered" in pruned[0]["extra"]["error"]


# ---- handle.results() reflects only done tasks ---------------------------- #
def test_handle_results_excludes_failures():
    async def fn(i):
        if i == 0:
            raise ValueError("x")
        return i
    flow = Flow()
    out = flow.map("w", range(3), fn)
    asyncio.run(flow.run())
    assert sorted(out.results()) == [1, 2]


# ---- best_of policy ------------------------------------------------------- #
def test_best_of_policy_picks_best_per_item():
    async def fn(u, *, attempt=0):
        return {"u": u, "v": u * 10 + attempt}
    flow = Flow()
    out = flow.map("solve", [1, 2], best_of(fn, n=3, score=lambda r: r["v"]))
    asyncio.run(flow.run())
    assert sorted(r["v"] for r in out.results()) == [12, 22]


def test_best_of_async_judge_and_raiser_dropping():
    async def fn(u, *, attempt=0):
        if attempt == 1:
            raise ValueError("bad")
        return attempt

    async def judge(results):
        return len(results) - 1                 # pick the last surviving attempt
    out = asyncio.run(best_of(fn, n=3, judge=judge)("u"))
    assert out == 2                             # attempt 1 raised and was dropped


def test_best_of_all_raise_returns_exception():
    async def fn(u, *, attempt=0):
        raise ValueError(f"boom {attempt}")
    out = asyncio.run(best_of(fn, n=2, score=lambda r: r)("u"))
    assert isinstance(out, ValueError)


def test_best_of_requires_exactly_one_selector():
    async def fn(u, *, attempt=0):
        return attempt
    for kwargs in ({}, {"judge": lambda r: 0, "score": lambda r: r}):
        try:
            best_of(fn, n=2, **kwargs)
            assert False, "expected ValueError"
        except ValueError:
            pass


# ---- with_retry policy ---------------------------------------------------- #
def test_with_retry_feeds_feedback_until_pass():
    seen = []

    async def fn(u, *, attempt=0, feedback=None):
        seen.append(feedback)
        return {"attempt": attempt}

    def check(r):
        ok = r["attempt"] >= 2
        return (ok, [] if ok else [f"attempt {r['attempt']} too low"])

    out = asyncio.run(with_retry(fn, check=check, max_attempts=5)("u"))
    assert out["attempt"] == 2
    assert seen[0] is None and seen[1] == ["attempt 0 too low"]


def test_with_retry_treats_raise_as_retryable():
    calls = []

    async def fn(u, *, attempt=0, feedback=None):
        calls.append((attempt, feedback))
        if attempt == 0:
            raise ValueError("first blew up")
        return {"attempt": attempt}

    out = asyncio.run(with_retry(fn, check=lambda r: (True, []), max_attempts=3)("u"))
    assert out["attempt"] == 1 and "first blew up" in calls[1][1][0]


def test_with_retry_returns_last_failing_when_exhausted():
    async def fn(u, *, attempt=0, feedback=None):
        return {"attempt": attempt}
    out = asyncio.run(
        with_retry(fn, check=lambda r: (False, ["nope"]), max_attempts=3)("u"))
    assert out["attempt"] == 2


def test_with_retry_policy_in_map():
    async def fn(u, *, attempt=0, feedback=None):
        return {"u": u, "attempt": attempt}

    def check(r):
        return (r["attempt"] >= 1, ["low"])

    flow = Flow()
    out = flow.map("solve", [1, 2], with_retry(fn, check=check, max_attempts=3))
    asyncio.run(flow.run())
    assert all(r["attempt"] == 1 for r in out.results())
