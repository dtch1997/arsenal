"""A fleet of coding agents as steps — with a *fake* backend so it runs anywhere
(no real `claude -p`). In real use, drop the fake and pass `flightdeck_backend()`
(or rely on the default `subprocess_backend`).

    agent(flow, ...)          -- one coding agent as a step -> AgentOutcome
    best_of(agent_fn, ...)    -- best-of-N agents on one task, keep the best patch
    with_retry(agent_fn, ...) -- retry an agent with feedback until it passes
    spawn over handles        -- merge the fleet's outcomes

    uv run python examples/agent_fleet.py
"""
from __future__ import annotations
import asyncio
import random
from pathlib import Path

from stagehand import (Flow, best_of, with_retry, agent, AgentOutcome,
                       AgentSpec, live_dashboard)


# --- a fake backend standing in for `claude -p` (swap for flightdeck_backend()) --- #
async def fake_backend(spec: AgentSpec) -> AgentOutcome:
    await asyncio.sleep(0.03)
    q = round(random.Random(spec.prompt).uniform(0, 1), 2)
    return AgentOutcome(ok=q > 0.3, summary=f"{spec.prompt[:32]} (q={q})", cost=q,
                        name=spec.name)


# --- an agent-fn for best_of/with_retry: `attempt` varies the try --- #
async def attempt_fix(task, *, attempt=0, feedback=None):
    note = f" | prev: {feedback}" if feedback else ""
    return await fake_backend(AgentSpec(prompt=f"{task} (try {attempt}){note}",
                                        name=f"fix#{attempt}"))


async def main():
    runs_dir = Path("runs-agents")

    f = Flow(runs_dir, title="agent fleet", concurrency=4)

    # one agent as a step
    summary = agent(f, "summarize the repo", backend=fake_backend, name="summarize")

    # best-of-4 agents on one task, keep the highest-quality patch
    best = f.spawn(best_of(attempt_fix, 4, score=lambda o: o.cost),
                   ("fix bug #42",), name="fix", type_fn=attempt_fix)

    # retry an agent with feedback until its output is acceptable
    fixed = f.spawn(with_retry(attempt_fix,
                               check=lambda o: (o.ok, ["still failing"]),
                               max_attempts=4),
                    ("make the flaky test pass",), name="stabilize",
                    type_fn=attempt_fix)

    # merge the fleet's outcomes
    f.spawn(lambda s, b, x: None, (summary, best, fixed), name="merge")

    async with live_dashboard(runs_dir, title="agent fleet") as status_html:
        state = await f.run()

    print(f"summarize: {summary.result.summary}")
    print(f"best-of-4:  {best.result.summary}")
    print(f"retried:    {fixed.result.summary} (ok={fixed.result.ok})")
    print(f"done — {state.done} ok, {state.failed} failed; open {status_html}")


if __name__ == "__main__":
    asyncio.run(main())
