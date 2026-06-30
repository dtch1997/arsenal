"""Unit tests for the orchestration skeleton (stage / gate / live_dashboard /
headless_handoff). Async tests drive their own loop via asyncio.run so there's no
pytest-asyncio dependency."""
import asyncio
import json

from stagehand import pipeline
from stagehand.monitor import monitor
from stagehand.pipeline import (stage, gate, best_of, with_retry, live_dashboard,
                                headless_handoff)


# ---- stage ---------------------------------------------------------------- #
def test_stage_preserves_unit_order():
    async def fn(u):
        await asyncio.sleep(0.01 * (5 - u))   # later units finish first...
        return u * 10
    out = asyncio.run(stage([0, 1, 2, 3], fn, concurrency=4))
    assert out == [0, 10, 20, 30]             # ...but results stay in unit order


def test_stage_bounds_concurrency():
    live = 0
    peak = 0

    async def fn(_u):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return None

    asyncio.run(stage(range(10), fn, concurrency=3))
    assert peak <= 3


def test_stage_backstops_a_raise_into_a_returned_exception():
    async def fn(u):
        if u == 1:
            raise ValueError("boom")
        return u
    out = asyncio.run(stage([0, 1, 2], fn, concurrency=2))
    assert out[0] == 0 and out[2] == 2
    assert isinstance(out[1], ValueError)     # the batch isn't cancelled


# ---- gate ----------------------------------------------------------------- #
def test_gate_partitions_and_marks_failed_monitor(tmp_path):
    # two units, each with a real monitor file; one will flunk the gate
    for name in ("good", "bad"):
        with monitor(name, 1, tmp_path / name / "p.progress.json", min_interval=0) as m:
            m.update()
    results = [{"name": "good", "ok": True}, {"name": "bad", "ok": False}]

    def predicate(r):
        return (r["ok"], [] if r["ok"] else ["it is bad"])

    passed, failed = gate(results, predicate,
                          monitor_path=lambda r: tmp_path / r["name"] / "p.progress.json")
    assert [p["name"] for p in passed] == ["good"]
    assert failed[0][0]["name"] == "bad" and failed[0][1] == ["it is bad"]
    # the bad unit's monitor file was patched done -> failed with the gate reason
    s = json.loads((tmp_path / "bad" / "p.progress.json").read_text())
    assert s["state"] == "failed" and "it is bad" in s["extra"]["error"]
    # the good unit's monitor is untouched
    assert json.loads((tmp_path / "good" / "p.progress.json").read_text())["state"] == "done"


# ---- best_of -------------------------------------------------------------- #
def test_best_of_picks_highest_score_and_varies_by_attempt():
    async def fn(unit, *, attempt=0):
        return {"unit": unit, "attempt": attempt, "val": unit * 10 + attempt}

    # score keeps the highest val; with attempt-varying fn that's attempt n-1
    out = asyncio.run(stage([1, 2], best_of(fn, n=3, score=lambda r: r["val"]),
                            concurrency=2))
    assert [r["attempt"] for r in out] == [2, 2]      # best attempt index per unit
    assert [r["val"] for r in out] == [12, 22]


def test_best_of_runs_all_attempts():
    seen = set()

    async def fn(unit, *, attempt=0):
        seen.add(attempt)
        return attempt

    asyncio.run(best_of(fn, n=4, score=lambda r: r)("u"))
    assert seen == {0, 1, 2, 3}


def test_best_of_async_judge_chooses_index():
    async def fn(unit, *, attempt=0):
        return f"{unit}-{attempt}"

    async def judge(results):
        return len(results) - 1               # always pick the last attempt

    out = asyncio.run(best_of(fn, n=3, judge=judge)("x"))
    assert out == "x-2"


def test_best_of_works_with_plain_fn_ignoring_attempt():
    async def fn(unit):                        # no attempt kwarg — must still run
        return unit + 1

    out = asyncio.run(best_of(fn, n=3, score=lambda r: r)(10))
    assert out == 11


def test_best_of_drops_raisers_then_scores():
    async def fn(unit, *, attempt=0):
        if attempt == 1:
            raise ValueError("bad attempt")
        return attempt
    out = asyncio.run(best_of(fn, n=3, score=lambda r: r)("u"))
    assert out == 2                            # raiser dropped, max of {0, 2}


def test_best_of_all_raise_returns_first_exception():
    async def fn(unit, *, attempt=0):
        raise ValueError(f"boom {attempt}")
    out = asyncio.run(best_of(fn, n=2, score=lambda r: r)("u"))
    assert isinstance(out, ValueError)


def test_best_of_marks_losers_failed(tmp_path):
    async def fn(unit, *, attempt=0):
        path = tmp_path / f"{attempt}.progress.json"
        with monitor(f"att{attempt}", 1, path, min_interval=0) as m:
            m.update()
        return {"attempt": attempt, "val": attempt, "path": path}

    out = asyncio.run(best_of(fn, n=3, score=lambda r: r["val"],
                              monitor_path=lambda r: r["path"])("u"))
    assert out["attempt"] == 2
    # winner stays done, losers (0, 1) flipped to failed with the reason
    assert json.loads((tmp_path / "2.progress.json").read_text())["state"] == "done"
    for i in (0, 1):
        s = json.loads((tmp_path / f"{i}.progress.json").read_text())
        assert s["state"] == "failed" and "not selected" in s["extra"]["error"]


def test_best_of_requires_exactly_one_selector():
    async def fn(unit, *, attempt=0):
        return attempt
    for kwargs in ({}, {"judge": lambda r: 0, "score": lambda r: r}):
        try:
            best_of(fn, n=2, **kwargs)
            assert False, "expected ValueError"
        except ValueError:
            pass


# ---- with_retry ----------------------------------------------------------- #
def test_with_retry_feeds_feedback_until_pass():
    seen_feedback = []

    async def fn(unit, *, attempt=0, feedback=None):
        seen_feedback.append(feedback)
        return {"attempt": attempt}

    def check(r):
        ok = r["attempt"] >= 2
        return (ok, [] if ok else [f"attempt {r['attempt']} too low"])

    out = asyncio.run(with_retry(fn, check=check, max_attempts=5)("u"))
    assert out["attempt"] == 2
    assert seen_feedback[0] is None                       # first try: no feedback
    assert seen_feedback[1] == ["attempt 0 too low"]      # prior issues fed back
    assert seen_feedback[2] == ["attempt 1 too low"]


def test_with_retry_returns_last_failing_result_when_exhausted():
    async def fn(unit, *, attempt=0, feedback=None):
        return {"attempt": attempt}

    out = asyncio.run(
        with_retry(fn, check=lambda r: (False, ["nope"]), max_attempts=3)("u"))
    assert out["attempt"] == 2                            # last (still-failing) try


def test_with_retry_treats_raise_as_retryable_failure():
    calls = []

    async def fn(unit, *, attempt=0, feedback=None):
        calls.append((attempt, feedback))
        if attempt == 0:
            raise ValueError("first blew up")
        return {"attempt": attempt}

    out = asyncio.run(with_retry(fn, check=lambda r: (True, []), max_attempts=3)("u"))
    assert out["attempt"] == 1
    assert calls[1][0] == 1                               # retried after the raise
    assert "first blew up" in calls[1][1][0]             # repr(exc) fed back


def test_with_retry_custom_feedback_transform():
    seen = []

    async def fn(unit, *, attempt=0, feedback=None):
        seen.append(feedback)
        return {"attempt": attempt}

    def check(r):
        return (r["attempt"] >= 1, ["low"])

    asyncio.run(with_retry(fn, check=check, max_attempts=3,
                           feedback=lambda r, issues: f"fix: {issues[0]}")("u"))
    assert seen[1] == "fix: low"


def test_with_retry_marks_superseded(tmp_path):
    async def fn(unit, *, attempt=0, feedback=None):
        path = tmp_path / f"{attempt}.progress.json"
        with monitor(f"try{attempt}", 1, path, min_interval=0) as m:
            m.update()
        return {"attempt": attempt, "path": path}

    def check(r):
        return (r["attempt"] >= 1, ["retry me"])

    asyncio.run(with_retry(fn, check=check, max_attempts=3,
                           monitor_path=lambda r: r["path"])("u"))
    # attempt 0 superseded -> failed; passing attempt 1 stays done
    s0 = json.loads((tmp_path / "0.progress.json").read_text())
    assert s0["state"] == "failed" and "superseded" in s0["extra"]["error"]
    assert json.loads((tmp_path / "1.progress.json").read_text())["state"] == "done"


# ---- live_dashboard ------------------------------------------------------- #
def test_live_dashboard_writes_and_finalizes(tmp_path):
    async def body():
        async with live_dashboard(tmp_path, title="t", interval=0.05) as html_path:
            with monitor("u", 1, tmp_path / "u.progress.json", parent=None, min_interval=0) as m:
                await asyncio.sleep(0.08)     # let the writer tick at least once
                m.update()
            await asyncio.sleep(0.08)
            return html_path
        # context exit forces a final render

    html_path = asyncio.run(body())
    assert html_path.exists()
    html = html_path.read_text()
    assert "t" in html and "u" in html and "1 done" in html   # terminal state captured


# ---- headless_handoff ----------------------------------------------------- #
def test_headless_handoff_builds_expected_argv(monkeypatch):
    captured = {}

    class _Proc:
        async def wait(self):
            return 0

    async def fake_exec(*argv, cwd=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(pipeline.asyncio, "create_subprocess_exec", fake_exec)
    rc = asyncio.run(headless_handoff("do the thing", cwd="/repo",
                                      allowed_tools=("Workflow", "Bash")))
    assert rc == 0
    argv = captured["argv"]
    assert argv[0] == "claude" and "-p" in argv and "do the thing" in argv
    assert "Workflow,Bash" in argv          # allowlist joined with commas
    assert captured["cwd"] == "/repo"
