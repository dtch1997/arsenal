"""Live e2e for the Nebius backend — the acceptance gate before real use.

The Nebius twin of scripts/e2e_cluster.py (which gated the RunPod path):
provision a 2-node 1-GPU-per-node H100 cluster on one InfiniBand fabric,
push a tiny codebase, torchrun an all-reduce using ONLY the env bellhop
injects, pull results from rank 0, verify nothing is left running.

Needs (all env):
  NEBIUS_IAM_TOKEN    auth for the SDK (`nebius iam get-access-token` or SA)
  NEBIUS_PROJECT_ID   parent project (console → project id)
  NEBIUS_FABRIC       InfiniBand fabric for the region, e.g. fabric-2 (H100
                      eu-north1); H200 fabrics: see docs.nebius.com GPU clusters

Cost: 2 × 1×H100 preset, ~15 min wall clock ≈ a few dollars. The VM image
ships CUDA but not torch; setup pip-installs it (~2 min).

Run:  cd repos/arsenal && .venv/bin/python \
        .claude/worktrees/bellhop-nebius/packages/bellhop/scripts/e2e_nebius_cluster.py
"""

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
from datetime import timedelta
from uuid import uuid4

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from bellhop import NebiusClusterConfig, RunSpec, gc_nebius, run_cluster  # noqa: E402

TRAIN_PY = r"""
import json, os, pathlib, torch, torch.distributed as dist
dist.init_process_group("nccl")
rank, world = dist.get_rank(), dist.get_world_size()
t = torch.tensor([float(rank)], device=f"cuda:{int(os.environ['LOCAL_RANK'])}")
dist.all_reduce(t)
expect = world * (world - 1) / 2
print(f"rank={rank} sum={t.item()} expect={expect}", flush=True)
assert t.item() == expect, "all-reduce mismatch"
if rank == 0:
    pathlib.Path("results").mkdir(exist_ok=True)
    json.dump({"world_size": world, "allreduce_sum": t.item(),
               "node_rank": os.environ["NODE_RANK"],
               "primary_addr": os.environ["PRIMARY_ADDR"]},
              open("results/allreduce.json", "w"))
dist.destroy_process_group()
"""

RUN_CMD = (
    'torchrun --nnodes "$NUM_NODES" --node_rank "$NODE_RANK" '
    '--nproc_per_node "$NUM_TRAINERS" --rdzv_id e2e --rdzv_backend static '
    '--rdzv_endpoint "$PRIMARY_ADDR:$PRIMARY_PORT" train.py'
)


async def main() -> None:
    for var in ("NEBIUS_IAM_TOKEN", "NEBIUS_PROJECT_ID", "NEBIUS_FABRIC"):
        if not os.environ.get(var):
            sys.exit(f"missing env: {var} (see module docstring)")
    t0 = time.monotonic()
    run_prefix = f"bellhop-e2e-{uuid4().hex[:8]}"   # scope names + gc to THIS run
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / "train.py").write_text(TRAIN_PY)
        out = tempfile.mkdtemp(prefix="bellhop-nebius-e2e-")
        spec = RunSpec(
            slug="nebius-cluster-e2e", codebase=td, run=RUN_CMD,
            setup="python3 -m pip install -q torch numpy",
            results_subdir="results", local_out=out, gcs_base=None)
        config = NebiusClusterConfig(
            fabric=os.environ["NEBIUS_FABRIC"],
            gpu="H100", nodes=2, gpu_count=1,
            boot_disk_gb=200, max_lifetime=timedelta(hours=1),
            name=run_prefix,
        )
        res = await run_cluster(spec, config)
        print(f"\nrun_cluster returned: cluster={res.pod_id} exit={res.remote_exit} "
              f"({time.monotonic()-t0:.0f}s)")
        print("log tail:\n" + res.log_tail)
        payload = json.load(open(pathlib.Path(res.local_results) / "results" / "allreduce.json"))
        print("pulled results/allreduce.json:", payload)
        assert payload["allreduce_sum"] == 1.0 and payload["world_size"] == 2

    leftover = await gc_nebius(timedelta(seconds=0), name_prefix=run_prefix, dry_run=True)
    print("this run's resources remaining:", leftover or "none")
    assert not leftover, "teardown left resources behind!"
    print(f"\nE2E PASSED in {time.monotonic()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
