"""Pod-tier ferry stress — the bellhop+ferry pairing, end to end.

The division of labor this proves: bellhop is the control plane (provision,
check code in, run, retrieve, tear down) and ferry is the data plane (bulk
bytes move pod<->GCS directly, never through the driver box). Credentials
follow the same split: ``ferry.gcs_pod_env()`` mints a short-lived (~1 h)
access token locally and hands it to the pod as rclone env-config vars via
``RunSpec(env=...)`` — no long-lived secret ever touches the pod, and access
self-expires.

What the pod does (fresh RunPod GPU pod, PyPI install — the real user path):

    pip install ferry-sync
    python scripts/stress.py --tiers bulk,resume --bulk-prefix gcs:...

which exercises: ensure_rclone() bootstrap on a bare image, sustained-pull
throughput, kill -9 + re-pull + `rclone check` byte integrity. The stress
run.log comes back via bellhop's normal results pull.

Run from the arsenal workspace venv (needs bellhop + a RUNPOD_API_KEY):

    uv run --package ferry-sync python packages/ferry/scripts/stress_pod.py

Reference result — 2026-08-15, RTX 4090 COMMUNITY pod, ferry-sync 0.3.0
from PyPI, corpus nmo-ema-em/ (110 files, 7.1 GiB): PASSED end to end.
Bulk 7.12 GiB in 127 s (~57 MiB/s — community-pod network, ~3.4x slower
than the devbox's 195 MiB/s); kill -9 + re-pull verified, 110/110 files
matching; pod torn down clean. NB at ~57 MiB/s a 200 GB pull is ~1 h,
which brushes the gcs_pod_env token TTL — for the largest jobs use a
scoped service-account key (or a SECURE-cloud pod with faster network).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from bellhop import PodConfig, RunSpec, run

import ferry

BULK_PREFIX = "gcs:alignment-team-general-storage/daniel/jarvis/experiments/nmo-ema-em/"
FERRY_PKG = Path(__file__).resolve().parent.parent  # packages/ferry


async def main() -> None:
    spec = RunSpec(
        slug="ferry-stress-pod",
        codebase=str(FERRY_PKG),
        setup="pip install ferry-sync",
        run=(
            "python scripts/stress.py --workdir /workspace/ferry-stress-data "
            f"--tiers bulk,resume --bulk-prefix {BULK_PREFIX}"
        ),
        env=ferry.gcs_pod_env(),   # short-lived token; pod sees remote `gcs:`
        gcs_base=None,             # run.log only — nothing worth archiving
        timeout=1800,
    )
    cfg = PodConfig(gpu="RTX 4090", container_disk_gb=40)
    res = await run(spec, cfg, api_key=os.environ.get("RUNPOD_API_KEY"))
    print(f"pod {res.pod_id} exit {res.remote_exit}; results: {res.local_results}")
    print(res.log_tail)


if __name__ == "__main__":
    asyncio.run(main())
