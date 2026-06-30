"""Unit tests for coding-agent steps (agent / backends / worktree isolation).
The backends are faked so nothing actually spawns a real `claude -p`."""
import asyncio
import json
import subprocess
from pathlib import Path

from stagehand import (flow, do, fanout, retry, run, agent, current_monitor,
                       AgentOutcome, AgentSpec)
from stagehand import agents
from stagehand.monitor import read_monitors


async def fake_backend(spec):
    return AgentOutcome(ok=True, summary=f"did: {spec.prompt}", cost=0.1, name=spec.name)


# ---- agent() step --------------------------------------------------------- #
def test_agent_step_runs_with_backend():
    async def body():
        with flow():
            out = agent("do the thing", backend=fake_backend, name="a")
            await run()
            return out.result
    r = asyncio.run(body())
    assert isinstance(r, AgentOutcome) and r.ok and "do the thing" in r.summary


def test_agent_handle_is_typed_AgentOutcome():
    with flow() as f:
        out = agent("x", backend=fake_backend)
        assert out.elem_type is AgentOutcome
        f.check()                                  # no type complaints


def test_agent_prompt_built_from_upstream():
    async def body():
        with flow():
            issue = do(lambda: "issue-67", name="issue")
            out = agent(lambda i: f"fix {i}", issue, backend=fake_backend, name="fix")
            await run()
            return out.result.summary
    assert "fix issue-67" in asyncio.run(body())


# ---- composition: best-of-N agents, retry-on-failure ---------------------- #
def test_fanout_over_agents_picks_best():
    async def solve(task, *, attempt=0):
        return AgentOutcome(ok=True, summary=f"{task}-{attempt}", cost=float(attempt))

    async def body():
        with flow():
            best = fanout(solve, "t", n=3, score=lambda o: o.cost)   # attempt 2 wins
            await run()
            return best.result
    assert asyncio.run(body()).summary == "t-2"


def test_retry_over_agent_until_ok():
    seen = []

    async def solve(task, *, attempt=0, feedback=None):
        seen.append((attempt, feedback))
        return AgentOutcome(ok=(attempt >= 1), summary=f"try{attempt}")

    async def body():
        with flow():
            out = retry(solve, "t", check=lambda o: (o.ok, ["not done"]), max_attempts=3)
            await run()
            return out.result
    r = asyncio.run(body())
    assert r.ok and r.summary == "try1"
    assert seen[1][1] == ["not done"]              # prior failure fed back


def test_set_default_backend_is_used():
    async def body():
        agents.set_default_backend(fake_backend)
        try:
            with flow():
                out = agent("hi", name="a")        # no explicit backend
                await run()
                return out.result
        finally:
            agents.set_default_backend(agents.subprocess_backend)
    assert asyncio.run(body()).ok


# ---- worktree isolation --------------------------------------------------- #
def test_agent_worktree_isolation_captures_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    async def writer(spec):                         # "agent" edits a file in its worktree
        (Path(spec.cwd) / "new.txt").write_text("agent was here\n")
        return AgentOutcome(ok=True, summary="wrote new.txt")

    async def body():
        with flow():
            out = agent("write a file", backend=writer, isolation="worktree",
                        cwd=str(repo), name="w")
            await run()
            return out.result
    r = asyncio.run(body())
    assert r.diff and "new.txt" in r.diff and "agent was here" in r.diff
    # base repo untouched, worktree cleaned up
    assert not (repo / "new.txt").exists()
    wts = subprocess.run(["git", "worktree", "list"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "stagehand-agent-" not in wts


# ---- subprocess backend --------------------------------------------------- #
def test_subprocess_backend_argv_and_parse(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        async def communicate(self):
            return (json.dumps({"result": "done", "total_cost_usd": 0.2,
                                "session_id": "abc"}).encode(), b"")

    async def fake_exec(*argv, cwd=None, stdout=None, stderr=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(agents.asyncio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(agents.subprocess_backend(
        AgentSpec(prompt="hi", name="x", cwd="/r")))
    a = captured["argv"]
    assert a[0] == "claude" and "-p" in a and "hi" in a and "json" in a
    assert captured["cwd"] == "/r"
    assert out.ok and out.summary == "done" and out.cost == 0.2 and out.session_id == "abc"


# ---- current_monitor: a step streams its own progress --------------------- #
def test_current_monitor_lets_a_step_update_itself(tmp_path):
    async def step():
        m = current_monitor()
        if m is not None:
            m.set(note="hello")
        return 1

    async def body():
        with flow(tmp_path):
            do(step, name="s")
            await run()
    asyncio.run(body())
    mons = [m for m in read_monitors(tmp_path) if m["name"].startswith("s/")]
    assert mons and mons[0]["extra"].get("note") == "hello"
