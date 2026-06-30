"""Unit tests for stagehand's logging (stdlib `logging`, silent by default)."""
import asyncio
import logging

from stagehand import Flow, enable_logging


async def ident(x):
    return x


def test_logger_has_a_nullhandler_silent_by_default():
    lg = logging.getLogger("stagehand")
    assert any(isinstance(h, logging.NullHandler) for h in lg.handlers)


def test_flow_logs_start_and_finish(caplog):
    caplog.set_level(logging.INFO, logger="stagehand")
    f = Flow(title="t")
    f.map("a", [1, 2], ident)
    asyncio.run(f.run())
    msgs = [r.getMessage() for r in caplog.records if r.name == "stagehand"]
    assert any("starting" in m and "2 tasks" in m for m in msgs)
    assert any("done in" in m and "2 ok" in m for m in msgs)


def test_task_failure_logs_a_warning(caplog):
    caplog.set_level(logging.DEBUG, logger="stagehand")

    async def boom(x):
        raise ValueError("nope")

    f = Flow()
    f.map("a", [1], boom)
    asyncio.run(f.run())
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warns and "failed" in warns[0].getMessage() and "nope" in warns[0].getMessage()


def test_filtered_and_skipped_log_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="stagehand")

    async def tenx(x):
        return x * 10

    f = Flow()
    src = f.map("src", [1, 2], ident)
    keep = f.filter("keep", src, lambda x: x == 1)      # prunes 2
    f.map("out", keep, tenx)                             # out for 2 is skipped
    asyncio.run(f.run())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("pruned" in m for m in msgs)
    assert any("skipped" in m for m in msgs)


def test_stop_when_logs_at_info(caplog):
    caplog.set_level(logging.INFO, logger="stagehand")

    async def slow(i):
        await asyncio.sleep(0.01 * (i + 1))
        return i

    f = Flow(concurrency=2)
    f.map("a", range(20), slow)
    asyncio.run(f.run(stop_when=lambda s: s.done >= 2))
    assert any("stop_when met" in r.getMessage() for r in caplog.records)


def test_enable_logging_is_idempotent():
    lg = enable_logging("DEBUG")
    try:
        n1 = sum(getattr(h, "_stagehand", False) for h in lg.handlers)
        enable_logging("INFO")
        n2 = sum(getattr(h, "_stagehand", False) for h in lg.handlers)
        assert n1 == 1 and n2 == 1                       # no stacked handlers
    finally:
        for h in list(lg.handlers):                      # leave the logger silent again
            if getattr(h, "_stagehand", False):
                lg.removeHandler(h)
        lg.setLevel(logging.NOTSET)
