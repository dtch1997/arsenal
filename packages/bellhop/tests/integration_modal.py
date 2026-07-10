"""Live end-to-end test on a REAL Modal sandbox (uses Modal compute / $).

Skipped by default. Run explicitly with:
    MODAL_LIVE=1 pytest tests/integration_modal.py -s
(needs the `modal` extra installed and Modal auth configured — e.g.
`modal token new`, or MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in the env).

Mirrors integration_live.py (the RunPod path) against the same _testcode, so a
green run proves the backend swap is a one-config-line change.
"""
import asyncio
import os
import time
from datetime import timedelta

import pytest

pytest.importorskip("modal")

from bellhop import ModalConfig, RunSpec, run  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("MODAL_LIVE"),
    reason="set MODAL_LIVE=1 to run the live Modal sandbox test",
)


async def _run():
    t0 = time.time()
    spec = RunSpec(
        slug="bellhop-modal-selftest",
        codebase="./_testcode",
        run="python go.py",
        env={"MY_SECRET": "s3cr3t-xyz"},  # validates env-injection (should appear in out.txt)
        gcs_base=None,                     # keep the test self-contained (no gcloud needed)
    )
    cfg = ModalConfig(
        gpu=None,                          # CPU sandbox — cheap; default debian-slim image
        timeout=timedelta(minutes=15),
    )
    res = await run(spec, cfg)
    print("=== MODAL TEST RESULT ===")
    print("elapsed_s:", round(time.time() - t0))
    print("box_id:", res.pod_id)
    print("remote_exit:", res.remote_exit)
    print("log_tail:\n" + res.log_tail)
    assert res.remote_exit == 0
    assert "MY_SECRET=s3cr3t-xyz" in res.log_tail  # env-injection worked
    assert os.path.exists(os.path.join(res.local_results, "results", "out.txt"))  # pull worked


def test_live_modal_end_to_end():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
