"""Live end-to-end test: provisions a REAL RTX 4090 community pod (costs $).

Skipped by default. Run explicitly with:
    RUNPOD_LIVE=1 pytest tests/integration_live.py -s
(needs RUNPOD_API_KEY, an ~/.ssh/id_ed25519 keypair, and gcloud on PATH).
"""
import asyncio
import os
import time
from datetime import timedelta

import pytest

from bellhop import PodConfig, RunSpec, SshProbe, run

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUNPOD_LIVE"),
    reason="set RUNPOD_LIVE=1 to run the billed live pod test",
)


async def _run():
    t0 = time.time()
    spec = RunSpec(
        slug="rpr-selftest",
        codebase="./_testcode",
        run="python go.py",
        env={"MY_SECRET": "s3cr3t-xyz"},  # validates env-injection (should appear in out.txt)
        gcs_base="gs://alignment-team-general-storage/daniel/jarvis/experiments",
    )
    cfg = PodConfig(
        compute="gpu",
        gpu_id="NVIDIA GeForce RTX 4090",
        cloud="COMMUNITY",
        container_disk_gb=20,
        ready=SshProbe("true"),
        provision_timeout=timedelta(seconds=600),
        ready_timeout=timedelta(seconds=600),
    )
    res = await run(spec, cfg)
    print("=== TEST RESULT ===")
    print("elapsed_s:", round(time.time() - t0))
    print("pod_id:", res.pod_id)
    print("remote_exit:", res.remote_exit)
    print("gcs_uri:", res.gcs_uri)
    print("retrieve:", res.retrieve_cmd)
    print("log_tail:\n" + res.log_tail)
    assert res.remote_exit == 0
    assert "MY_SECRET=s3cr3t-xyz" in res.log_tail  # env-injection worked


def test_live_end_to_end():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
