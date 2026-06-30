"""Unit tests for the imperative-reading DSL (flow / do / fanout / retry / each /
run). Async tests drive their own loop via asyncio.run."""
import asyncio

from stagehand import flow, do, fanout, retry, each, run


def test_do_chain_infers_dependency():
    async def body():
        with flow():
            a = do(lambda: 5, name="a")
            b = do(lambda x: x + 1, a, name="b")
            c = do(lambda x: x * 2, b, name="c")
            await run()
            return a.result, b.result, c.result
    assert asyncio.run(body()) == (5, 6, 12)


def test_do_with_literal_and_handle_args():
    async def body():
        with flow():
            a = do(lambda: 10, name="a")
            b = do(lambda x, k: x + k, a, k=3, name="b")     # handle + literal kwarg
            await run()
            return b.result
    assert asyncio.run(body()) == 13


def test_do_over_list_of_handles_gathers():
    async def body():
        with flow():
            parts = each(lambda x: x, range(4))               # 4 one-task handles
            total = do(lambda xs: sum(xs), parts, name="sum")
            await run()
            return total.result
    assert asyncio.run(body()) == 6


def test_do_over_list_reduces_over_survivors():
    async def body():
        async def maybe(i):
            if i == 1:
                raise ValueError("x")
            return i
        with flow():
            parts = [do(maybe, i, name="p") for i in range(3)]
            total = do(lambda xs: sum(xs), parts, name="sum")   # gathers survivors
            state = await run()
            return total.result, state
    total, state = asyncio.run(body())
    assert total == 0 + 2 and state.failed == 1   # the raiser dropped from the sum


def test_after_is_ordering_only_dependency():
    order = []

    async def body():
        async def a():
            await asyncio.sleep(0.02)
            order.append("a")
            return 1

        async def b():
            order.append("b")
            return 2

        with flow():
            ha = do(a, name="a")
            do(b, after=[ha], name="b")                       # b waits for a, ignores value
            await run()
    asyncio.run(body())
    assert order == ["a", "b"]


def test_fanout_keeps_best():
    async def body():
        async def sample(seed, *, attempt=0):
            return {"v": seed + attempt}
        with flow():
            base = do(lambda: 100, name="base")
            best = fanout(sample, base, n=4, score=lambda r: r["v"])
            await run()
            return best.result
    assert asyncio.run(body()) == {"v": 103}


def test_retry_until_check_passes():
    async def body():
        async def draft(x, *, attempt=0, feedback=None):
            return {"len": 3 + attempt * 4, "attempt": attempt}
        with flow():
            seed = do(lambda: "x", name="seed")
            out = retry(draft, seed, check=lambda r: (r["len"] >= 10, ["short"]))
            await run()
            return out.result
    r = asyncio.run(body())
    assert r["len"] >= 10 and r["attempt"] == 2


def test_eager_async_inside_a_node():
    # plain await / if work inside a node fn — the "eager inside nodes" path
    async def body():
        async def pipeline(seed):
            await asyncio.sleep(0.01)
            if seed % 2 == 0:
                return "even"
            return "odd"
        with flow():
            out = do(pipeline, 4, name="p")
            await run()
            return out.result
    assert asyncio.run(body()) == "even"


def test_failed_dep_skips_dependent():
    async def body():
        async def boom():
            raise ValueError("x")
        with flow() as f:
            a = do(boom, name="a")
            do(lambda x: x + 1, a, name="b")
            state = await run()
            return f, state
    f, state = asyncio.run(body())
    assert state.failed == 1 and state.skipped == 1


def test_do_outside_flow_raises():
    try:
        do(lambda: 1)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_dsl_writes_monitor_files(tmp_path):
    async def body():
        with flow(tmp_path, title="dsl"):
            a = do(lambda: 1, name="alpha")
            do(lambda x: x + 1, a, name="beta")
            await run()
    asyncio.run(body())
    from stagehand.monitor import read_monitors
    names = {m["name"] for m in read_monitors(tmp_path)}
    assert any(n.startswith("alpha") for n in names)
    assert any(n.startswith("beta") for n in names)
