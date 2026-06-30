"""A worked sweep on the stagehand engine — the shape of a real experiment, with
the compute faked out so it runs anywhere in a couple of seconds.

    train ──> gate(filter) ──> eval ──> manifest(reduce)

You *declare* the DAG; the engine streams it — eval(cell_0) starts the moment
train(cell_0) is healthy, while train(cell_2) is still going. There's no barrier
until `reduce`, which needs the whole eval stage to write the manifest. Every task
writes a monitor file, so `live_dashboard` shows the live graph. Run it:

    uv run python examples/sweep.py
"""
from __future__ import annotations
import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from stagehand import Flow, live_dashboard


@dataclass
class Cell:
    name: str
    seed: int
    steps: int = 20


# ---- node fns (fake compute) --------------------------------------------- #
async def train_one(cell: Cell):
    loss = 2.0
    for _ in range(cell.steps):
        await asyncio.sleep(0.01)
        loss *= 0.95
    # seed 2 deliberately produces a dud checkpoint to exercise the gate
    ckpt = "" if cell.seed == 2 else f"fake://{cell.name}"
    return {"cell": cell, "checkpoint": ckpt, "loss": round(loss, 3)}


def is_healthy(t):
    ok = (t["checkpoint"] or "").startswith("fake://")
    return (ok, [] if ok else ["no/invalid checkpoint"])


async def eval_one(t):
    cell = t["cell"]
    await asyncio.sleep(0.02)
    accept = round(random.Random(cell.seed).uniform(0, 1), 2)
    return {"cell": cell.name, "seed": cell.seed, "accept": accept}


async def main():
    runs_dir = Path("runs")
    cells = [Cell(f"cell_s{s}", seed=s) for s in range(3)]

    flow = Flow(runs_dir, title="example sweep", concurrency=4)
    trained = flow.map("train", cells, train_one)
    healthy = flow.filter("gate", trained, is_healthy)         # drop the dud, stream survivors
    evals = flow.map("eval", healthy, eval_one)                # eval_i waits on train_i only
    manifest = flow.reduce("manifest", evals, lambda rs: rs)   # the one barrier

    async with live_dashboard(runs_dir, title="example sweep") as status_html:
        state = await flow.run()

    (runs_dir / "manifest.json").write_text(
        json.dumps(manifest.results()[0], indent=2))
    print(f"done — {state.done} tasks ok, {state.failed} failed, "
          f"{state.skipped} skipped")
    print(f"open {status_html} ; manifest at {runs_dir / 'manifest.json'}")

    # Optional tail: hand the manifest to a headless Claude.
    # from stagehand import headless_handoff
    # await headless_handoff("Invoke Workflow on runs/manifest.json and summarize.",
    #                        cwd=".")


if __name__ == "__main__":
    asyncio.run(main())
