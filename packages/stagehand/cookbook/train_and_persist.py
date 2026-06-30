"""Recipe: run a training/eval job, validate it, persist the artifact, record a
pointer — reliably and idempotently.

The reliability of a compute step lives in four places; this recipe wires each to
a stagehand primitive:

    body         a step that runs the job and returns its outputs        do(...)
    criterion    exit-ok ∧ artifact produced ∧ metrics finite            filter + checks
    persistence  upload the artifact, return a *pointer* not the bytes   a persist step
    idempotency  skip the whole thing if the pointer already exists       a guard at the top

The job/persist/pointer pieces are **seams** — swap the fakes here for your real
backend (bellhop/RunPod, Modal, …), your real sink (GCS/S3 via ferry/cloudfs), and
your real pointer location (a file you commit). The shape stays identical.

Run it twice: the second run skips every cell (pointers already exist).

    uv run python cookbook/train_and_persist.py
"""
from __future__ import annotations
import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from stagehand import Flow, live_dashboard
from stagehand.checks import produced, finite, exit_ok


@dataclass
class Cfg:
    slug: str
    seed: int


# --------------------------------------------------------------------------- #
# SEAMS — replace these three with your real tools. Everything else is generic.
# --------------------------------------------------------------------------- #
LOCAL = Path("runs-cookbook")                 # stands in for gs://…/experiments/
REMOTE = LOCAL / "remote"                     # stands in for the bucket


async def run_job(cfg: Cfg, workdir: Path) -> dict:
    """YOUR backend: launch the job, return {exit, ckpt, metrics}. (Faked here.)"""
    await asyncio.sleep(0.05)
    rng = random.Random(cfg.seed)
    ckpt = workdir / "model.ckpt"
    loss = float("nan") if cfg.seed == 2 else round(rng.uniform(0.1, 0.9), 3)  # seed 2 diverges
    if cfg.seed != 3:                          # seed 3 fails to write a checkpoint
        ckpt.write_text(f"weights for {cfg.slug}\n")
    return {"exit": 0, "ckpt": ckpt, "metrics": {"loss": loss}}


def upload(local: Path, dest: str) -> str:
    """YOUR sink: copy bytes to remote storage, return the URI. (Faked: a copy.)"""
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())
    return f"file://{target}"                   # in real life: gs://…/<slug>/model.ckpt


def pointer_file(slug: str) -> Path:
    """Where the committed pointer lives (a tiny file you check into the repo)."""
    return LOCAL / "pointers" / f"{slug}.uri"


# --------------------------------------------------------------------------- #
# THE RECIPE — generic; reads only through the seams above.
# --------------------------------------------------------------------------- #
async def train(cfg: Cfg) -> dict:
    # idempotency: a finished cell already has a committed pointer — skip the job.
    ptr = pointer_file(cfg.slug)
    if ptr.exists():
        return {"cfg": cfg, "uri": ptr.read_text().strip(), "skipped": True}
    workdir = LOCAL / cfg.slug
    workdir.mkdir(parents=True, exist_ok=True)
    out = await run_job(cfg, workdir)
    return {"cfg": cfg, "skipped": False, **out}


def healthy(r: dict):
    # the criterion: the job exited cleanly, wrote a checkpoint, and didn't diverge.
    if r.get("skipped"):
        return True                            # already validated on a previous run
    return exit_ok(r["exit"]) & produced(r["ckpt"]) & finite(r["metrics"]["loss"])


async def persist(r: dict) -> dict:
    if r.get("skipped"):
        return r
    cfg = r["cfg"]
    uri = upload(r["ckpt"], str(REMOTE / cfg.slug / "model.ckpt"))
    ptr = pointer_file(cfg.slug)               # record the pointer (commit this file)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(uri)
    return {"cfg": cfg, "uri": uri, "skipped": False}


async def main():
    cfgs = [Cfg(f"cell_s{s}", seed=s) for s in range(5)]   # seeds 2 & 3 are unhealthy

    flow = Flow(LOCAL, title="train + persist", concurrency=4)
    trained = flow.map("train", cfgs, train)
    good = flow.filter("gate", trained, healthy)           # drop diverged / no-checkpoint
    persisted = flow.map("persist", good, persist)
    manifest = flow.reduce("manifest", persisted,
                           lambda rs: {r["cfg"].slug: r["uri"] for r in rs})

    async with live_dashboard(LOCAL, title="train + persist") as status_html:
        state = await flow.run(check=True)                 # type-check the graph first

    (LOCAL / "manifest.json").write_text(json.dumps(manifest.result, indent=2))
    skipped = sum(1 for r in persisted.results() if r.get("skipped"))
    print(f"persisted {len(persisted.results())} cells ({skipped} skipped as idempotent); "
          f"{state.failed} failed the gate")
    print(f"manifest: {manifest.result}")
    print(f"open {status_html}  ·  run again to see idempotent skips")


if __name__ == "__main__":
    asyncio.run(main())
