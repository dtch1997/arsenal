"""Node policies + dynamic fan-out on the stagehand engine, with the compute faked
so it runs anywhere in a couple of seconds.

    best_of(fn, n, score=...)   -- each item runs N attempts, keep the best
    with_retry(fn, check=...)   -- retry an item with feedback until it passes
    Flow.expand(...)            -- one upstream result fans out into many tasks

best_of / with_retry are just unit-fns you drop into `Flow.map`, so they show up
as ordinary nodes on the dashboard. Run it:

    uv run python examples/fanout_retry.py
"""
from __future__ import annotations
import asyncio
import json
import random
from pathlib import Path

from stagehand import Flow, best_of, with_retry, live_dashboard


# ---- best_of: N sampled attempts per prompt, keep the highest reward ------- #
async def sample(prompt, *, attempt=0):
    await asyncio.sleep(0.02)
    reward = round(random.Random(f"{prompt}-{attempt}").uniform(0, 1), 2)
    return {"prompt": prompt, "attempt": attempt, "reward": reward}


# ---- with_retry: redraft until the draft is long enough ------------------- #
async def draft(prompt, *, attempt=0, feedback=None):
    await asyncio.sleep(0.02)
    return {"prompt": prompt, "attempt": attempt, "length": 3 + attempt * 4}


def long_enough(d):
    ok = d["length"] >= 10
    return (ok, [] if ok else [f"too short ({d['length']} < 10)"])


# ---- expand: each prompt fans out into a data-dependent number of shards --- #
async def plan(prompt):
    await asyncio.sleep(0.01)
    return {"prompt": prompt, "shards": len(prompt) % 3 + 1}


async def run_shard(shard):
    await asyncio.sleep(0.01)
    return f"{shard['prompt']}#{shard['i']}"


async def main():
    runs_dir = Path("runs-fanout-retry")
    prompts = ["alpha", "bravo", "charlie"]

    flow = Flow(runs_dir, title="fan-out + retry", concurrency=4)

    # fan out 4 attempts per prompt, keep the best reward
    best = flow.map("best", prompts,
                    best_of(sample, n=4, score=lambda r: r["reward"]))
    # retry each prompt with feedback until it's long enough
    drafts = flow.map("draft", prompts,
                      with_retry(draft, check=long_enough, max_attempts=4))
    # plan -> expand into a data-dependent number of shards -> run each shard
    planned = flow.map("plan", prompts, plan)
    shards = flow.expand("shards", planned,
                         lambda p: [{"prompt": p["prompt"], "i": i}
                                    for i in range(p["shards"])])
    ran = flow.map("shard", shards, run_shard)

    async with live_dashboard(runs_dir, title="fan-out + retry") as status_html:
        await flow.run()

    print("[best_of]   " + ", ".join(
        f"{b['prompt']}=#{b['attempt']}({b['reward']})" for b in best.results()))
    print("[with_retry]" + ", ".join(
        f" {d['prompt']}=len{d['length']}@try{d['attempt']}" for d in drafts.results()))
    print(f"[expand]    {len(ran.results())} shards from {len(prompts)} prompts")

    (runs_dir / "manifest.json").write_text(json.dumps(
        {"best": best.results(), "drafts": drafts.results(),
         "shards": ran.results()}, indent=2, default=str))
    print(f"done — open {status_html}")


if __name__ == "__main__":
    asyncio.run(main())
