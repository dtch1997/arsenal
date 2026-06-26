"""CLI over run() — one codebase on an ephemeral RunPod pod or Modal sandbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta

from .errors import BellhopError
from .modal_box import ModalConfig
from .pod import PodConfig
from .run import DEFAULT_GCS_BASE, RunSpec, run


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bellhop", description="Run a codebase on an ephemeral RunPod pod or Modal sandbox.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="provision -> run -> pull -> GCS -> teardown")
    r.add_argument("--backend", choices=["runpod", "modal"], default="runpod")
    # shared
    r.add_argument("--slug", required=True)
    r.add_argument("--codebase", required=True, help="local dir OR git URL")
    r.add_argument("--run", required=True, help="the command(s) to run")
    r.add_argument("--setup", default=None, help="deps, run before --run")
    r.add_argument("--image", default=None, help="image (RunPod docker ref / Modal registry ref)")
    r.add_argument("--image-preset", default=None, help="RunPod: cpu-base/pytorch-* ; Modal: debian-slim/pytorch-cuda")
    r.add_argument("--results-subdir", default="results")
    r.add_argument("--local-out", default=None)
    r.add_argument("--gcs-base", default=DEFAULT_GCS_BASE)
    r.add_argument("--no-gcs", action="store_true", help="skip GCS upload")
    r.add_argument("--env-json", default=None, help="JSON object of extra box env vars")
    r.add_argument("--keep-pod", action="store_true", help="leave the box up after the run")
    # RunPod-specific
    r.add_argument("--compute", choices=["cpu", "gpu"], default="cpu")
    r.add_argument("--gpu-id", default=None, help="RunPod GPU, e.g. 'NVIDIA GeForce RTX 4090'")
    r.add_argument("--container-disk-gb", type=int, default=20)
    r.add_argument("--cloud", choices=["SECURE", "COMMUNITY"], default="COMMUNITY")
    r.add_argument("--ready-timeout", type=int, default=420)
    # Modal-specific
    r.add_argument("--gpu", default=None, help="Modal GPU, e.g. 'A10G', 'A100', 'H100', 'T4', 'L4'")
    r.add_argument("--pip", action="append", default=None, help="Modal: pip-install onto the image (repeatable)")
    r.add_argument("--timeout-hours", type=float, default=24.0, help="Modal sandbox hard max lifetime")
    return p


def _build_backend(args, env: dict):
    if args.backend == "modal":
        return ModalConfig(
            gpu=args.gpu,
            image=args.image,
            image_preset=args.image_preset,
            pip=list(args.pip or []),
            env=dict(env),
            timeout=timedelta(hours=args.timeout_hours),
        )
    return PodConfig(
        compute=args.compute,
        gpu_id=args.gpu_id,
        image=args.image,
        image_preset=args.image_preset,
        container_disk_gb=args.container_disk_gb,
        cloud=args.cloud,
        env=dict(env),
        ready_timeout=timedelta(seconds=args.ready_timeout),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = json.loads(args.env_json) if args.env_json else {}

    backend = _build_backend(args, env)
    spec = RunSpec(
        slug=args.slug,
        codebase=args.codebase,
        run=args.run,
        setup=args.setup,
        results_subdir=args.results_subdir,
        local_out=args.local_out,
        gcs_base=None if args.no_gcs else args.gcs_base,
        env=dict(env),
    )

    try:
        res = asyncio.run(run(spec, backend, keep_pod=args.keep_pod))
    except BellhopError as e:
        print(f"ERROR [{type(e).__name__}]: {e}", file=sys.stderr)
        return e.exit_code

    print("\n===================== BELLHOP RESULT =====================")
    print(f"backend:       {args.backend}")
    print(f"slug:          {res.slug}")
    print(f"box_id:        {res.pod_id} (torn down: {'no' if args.keep_pod else 'yes'})")
    print(f"remote_exit:   {res.remote_exit}")
    print(f"local_results: {res.local_results}")
    print(f"gcs_artifacts: {res.gcs_uri}")
    if res.retrieve_cmd:
        print(f"retrieve:      {res.retrieve_cmd}")
    print("-------------------- run.log (tail) --------------------")
    print(res.log_tail or "(no run.log)")
    print("=======================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
