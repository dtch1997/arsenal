"""Recipe: the RUN step — execute code to produce an artifact, validate it,
persist it, record a pointer. Idempotent.

ONE recipe covers every run — training, eval, plotting, report-building. Only two
things vary per instance: the **job** (what to execute) and the **check** (what
makes its artifact valid — `finite` metrics for a train run, `valid_image` for a
plot, `json_has` for an eval). Everything else is identical:

    body         run the job -> artifact                       a map step (+ idempotent guard)
    criterion    exit-ok ∧ <artifact is valid>                 filter + checks
    persist      upload the artifact, record a *pointer*       a persist step
    idempotency  skip if the pointer already exists            a guard at the top

The job / upload / pointer pieces are **seams** — swap the fakes for your backend
(bellhop/RunPod, Modal), sink (GCS/S3 via ferry/cloudfs), and committed pointer
location. Runs with fakes; run it twice to see idempotent skips.

    uv run python cookbook/run_step.py
"""
from __future__ import annotations
import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

from stagehand import Flow, live_dashboard
from stagehand.checks import exit_ok, finite, valid_image

LOCAL = Path("runs-cookbook")
REMOTE = LOCAL / "remote"                      # stands in for the bucket


@dataclass
class Cfg:
    slug: str
    seed: int


# ---- seams (swap for your stack) ------------------------------------------ #
def upload(local: Path, dest: str) -> str:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(local.read_bytes())
    return f"file://{target}"                   # real life: gs://…/<slug>/<artifact>


def pointer_file(slug: str, step: str) -> Path:
    return LOCAL / "pointers" / f"{step}.{slug}.uri"


# ---- the generic recipe --------------------------------------------------- #
def run_and_persist(flow, step, cfgs, *, job, check, artifact_dest):
    """Add a run step: `job(cfg) -> {exit, artifact, ...}` → gate on
    `exit_ok & check(result)` → persist the artifact + record a pointer. Idempotent.
    Returns the persisted-handle (each result has a `uri`)."""

    async def body(cfg):
        ptr = pointer_file(cfg.slug, step)
        if ptr.exists():                         # idempotent: already done
            return {"cfg": cfg, "uri": ptr.read_text().strip(), "skipped": True}
        out = await job(cfg)
        return {"cfg": cfg, "skipped": False, **out}

    def valid(r):
        if r.get("skipped"):
            return True                          # validated on a previous run
        return exit_ok(r["exit"]) & check(r)     # exit-ok ∧ the artifact-specific check

    async def persist(r):
        if r.get("skipped"):
            return r
        uri = upload(r["artifact"], artifact_dest(r["cfg"]))
        ptr = pointer_file(r["cfg"].slug, step)
        ptr.parent.mkdir(parents=True, exist_ok=True)
        ptr.write_text(uri)                      # commit this pointer in real use
        return {"cfg": r["cfg"], "uri": uri, "skipped": False}

    produced = flow.map(step, cfgs, body)
    good = flow.filter(f"{step}-gate", produced, valid)
    return flow.map(f"{step}-persist", good, persist)


# ---- two instances of the SAME recipe ------------------------------------- #
async def train_job(cfg: Cfg) -> dict:
    await asyncio.sleep(0.03)
    wd = LOCAL / cfg.slug
    wd.mkdir(parents=True, exist_ok=True)
    ckpt = wd / "model.ckpt"
    loss = float("nan") if cfg.seed == 2 else round(random.Random(cfg.seed).uniform(.1, .9), 3)
    if cfg.seed != 3:                            # seed 3 fails to write a checkpoint
        ckpt.write_text(f"weights {cfg.slug}\n")
    return {"exit": 0, "artifact": ckpt, "metrics": {"loss": loss}}


async def plot_job(cfg: Cfg) -> dict:
    await asyncio.sleep(0.02)
    wd = LOCAL / cfg.slug
    wd.mkdir(parents=True, exist_ok=True)
    png = wd / "fig.png"
    if cfg.seed == 4:                            # seed 4 writes a corrupt image
        png.write_text("not a png")
    else:
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return {"exit": 0, "artifact": png}


async def main():
    cfgs = [Cfg(f"cell_s{s}", seed=s) for s in range(5)]

    flow = Flow(LOCAL, title="run steps", concurrency=4)
    # a *training* run: artifact valid iff its loss is finite
    trained = run_and_persist(flow, "train", cfgs, job=train_job,
                              check=lambda r: finite(r["metrics"]["loss"]),
                              artifact_dest=lambda c: str(REMOTE / c.slug / "model.ckpt"))
    # a *plotting* run: same recipe, artifact valid iff it's a real image
    plotted = run_and_persist(flow, "plot", cfgs, job=plot_job,
                              check=lambda r: valid_image(r["artifact"]),
                              artifact_dest=lambda c: str(REMOTE / c.slug / "fig.png"))

    async with live_dashboard(LOCAL, title="run steps") as status_html:
        state = await flow.run(check=True)

    manifest = {"train": {r["cfg"].slug: r["uri"] for r in trained.results()},
                "plot": {r["cfg"].slug: r["uri"] for r in plotted.results()}}
    (LOCAL / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"train: {len(trained.results())} persisted · plot: {len(plotted.results())} "
          f"persisted · {state.failed} failed the gate")
    print(f"open {status_html}  ·  run again to see idempotent skips")


if __name__ == "__main__":
    asyncio.run(main())
