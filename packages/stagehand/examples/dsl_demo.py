"""The same sweep written in the imperative-reading DSL — straight-line code that
builds the DAG, with the compute faked so it runs anywhere in a couple of seconds.

    do(train) ─> do(check) ─> do(eval)     (per cell; a raise in check prunes the rest)
    fanout / retry / after  for the richer shapes

`do`/`fanout`/`retry` return lazy handles; nothing runs until `run()`. Each shows
up on the dashboard. Run it:

    uv run python examples/dsl_demo.py
"""
from __future__ import annotations
import asyncio
import random
from dataclasses import dataclass
from pathlib import Path

from stagehand import flow, do, fanout, retry, run, live_dashboard


@dataclass
class Cell:
    name: str
    seed: int


async def train(cell: Cell):
    await asyncio.sleep(0.05)
    return {"cell": cell, "checkpoint": "" if cell.seed == 2 else f"fake://{cell.name}"}


async def check(t):
    # eager control flow *inside* a node: raise to prune (dependents skip)
    if not (t["checkpoint"] or "").startswith("fake://"):
        raise ValueError(f"{t['cell'].name}: no checkpoint")
    return t


async def evaluate(t):
    await asyncio.sleep(0.02)
    return {"cell": t["cell"].name, "accept": round(random.Random(t["cell"].seed).uniform(0, 1), 2)}


async def sample(prompt, *, attempt=0):
    await asyncio.sleep(0.02)
    return {"prompt": prompt, "attempt": attempt,
            "reward": round(random.Random(f"{prompt}-{attempt}").uniform(0, 1), 2)}


async def draft(prompt, *, attempt=0, feedback=None):
    await asyncio.sleep(0.02)
    return {"prompt": prompt, "length": 3 + attempt * 4}


async def main():
    runs_dir = Path("runs-dsl")
    cells = [Cell(f"cell_s{s}", seed=s) for s in range(3)]

    with flow(runs_dir, title="dsl sweep", concurrency=4):
        evals = []
        for cell in cells:
            ckpt = do(train, cell, name="train")
            good = do(check, ckpt, name="gate")          # raises -> eval skipped for seed 2
            evals.append(do(evaluate, good, name="eval"))
        summary = do(lambda rs: rs, evals, name="summary")   # gathers the survivors

        # the richer shapes, same `do`-like spelling
        best = fanout(sample, "alpha", n=4, score=lambda r: r["reward"])
        fixed = retry(draft, "bravo", check=lambda r: (r["length"] >= 10, ["short"]))
        do(lambda b, f: None, best, fixed, name="report", after=[summary])

        async with live_dashboard(runs_dir, title="dsl sweep") as status_html:
            state = await run()

    print(f"survivors: {len(summary.result)}  | best reward: {best.result['reward']}  "
          f"| draft len: {fixed.result['length']}")
    print(f"done — {state.done} ok, {state.failed} failed, {state.skipped} skipped; "
          f"open {status_html}")


if __name__ == "__main__":
    asyncio.run(main())
