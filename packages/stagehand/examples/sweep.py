"""A worked staircase on the stagehand API — the shape of a real experiment sweep,
with the compute faked out so it runs anywhere in a couple of seconds.

    train (barrier) -> GATE drop dead -> eval (barrier) -> GATE -> manifest

Mirrors a real driver: every unit runs under a `monitor` (so the live dashboard
shows it), one stage feeds the next only through a gate, and the whole thing is
watched by `live_dashboard`. Run it and open the printed status.html:

    uv run python examples/sweep.py
"""
from __future__ import annotations
import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from stagehand import stage, gate, live_dashboard, monitor


@dataclass
class Cell:
    name: str
    seed: int
    steps: int = 20


# ---- stage 1: "train" (fake) --------------------------------------------- #
async def train_one(cell: Cell, runs_dir: Path):
    rd = runs_dir / cell.name
    try:
        with monitor(cell.name, cell.steps, rd / "train.progress.json",
                     parent="sweep", meta={"phase": "train"}, min_interval=0) as m:
            loss = 2.0
            for _ in range(cell.steps):
                await asyncio.sleep(0.01)
                loss *= 0.95
                m.update(loss=round(loss, 3))
            # seed 2 deliberately produces a dud checkpoint to exercise the gate
            ckpt = "" if cell.seed == 2 else f"fake://{cell.name}"
            (rd / "checkpoint.txt").write_text(ckpt)
        return {"cell": cell, "dir": rd, "checkpoint": ckpt, "error": None}
    except Exception as e:                       # captured, not fatal
        return {"cell": cell, "dir": rd, "checkpoint": None, "error": repr(e)}


def gate_train(t):
    issues = []
    if t["error"]:
        issues.append(f"train raised: {t['error']}")
    if not (t["checkpoint"] or "").startswith("fake://"):
        issues.append("no/invalid checkpoint")
    return (not issues, issues)


# ---- stage 2: "eval" (fake) ---------------------------------------------- #
async def eval_one(t, runs_dir: Path):
    cell, rd = t["cell"], t["dir"]
    with monitor(f"{cell.name} · eval", 1, rd / "eval.progress.json",
                 parent=cell.name, meta={"phase": "eval"}, min_interval=0) as m:
        await asyncio.sleep(0.02)
        rng = random.Random(cell.seed)           # deterministic given the seed
        accept = round(rng.uniform(0, 1), 2)
        m.set(accept=accept, reject=round(1 - accept, 2))
        m.update()
    return {"cell": cell.name, "seed": cell.seed, "accept": accept}


async def main():
    runs_dir = Path("runs")
    cells = [Cell(f"cell_s{s}", seed=s) for s in range(3)]

    async with live_dashboard(runs_dir, title="example sweep") as status_html:
        # STAGE 1: train (barrier), then drop units that produced no checkpoint
        trained = await stage([c for c in cells], lambda c: train_one(c, runs_dir),
                              concurrency=2)
        healthy, failed = gate(trained, gate_train,
                               monitor_path=lambda r: r["dir"] / "train.progress.json")
        print(f"[gate] {len(healthy)}/{len(trained)} healthy; "
              f"dropped {[f[0]['cell'].name for f in failed]}")

        # STAGE 2: eval the survivors (barrier)
        evals = await stage(healthy, lambda h: eval_one(h, runs_dir), concurrency=4)

        manifest = {"healthy": [h["cell"].name for h in healthy],
                    "failed": [f[0]["cell"].name for f in failed],
                    "evals": evals}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"done — open {status_html} ; manifest at {runs_dir / 'manifest.json'}")


if __name__ == "__main__":
    asyncio.run(main())
