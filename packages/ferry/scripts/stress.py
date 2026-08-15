"""ferry stress test — throughput, kill+resume integrity, many-small-files.

Repeatable version of the manual pass run 2026-08-15. Three tiers, each
exercising a different failure regime of big transfers:

  bulk       sustained throughput on a GiB-scale tree of large files
  resume     kill -9 mid-pull, re-pull, then `rclone check` byte-integrity
  manyfiles  tens of thousands of small objects (request-rate-bound regime)

Usage (corpora are gs:// prefixes you can read; defaults are team-internal):

    python scripts/stress.py --workdir /tmp/ferry-stress \
        --bulk-prefix      gs://bucket/some/7gb-tree/ \
        --manyfiles-prefix gs://bucket/some/50k-object-tree/ \
        [--tiers bulk,resume,manyfiles]

The workdir is deleted and recreated per tier; nothing is written remotely.

Reference results — devbox (NFS-homed VM), team GCS bucket, 2026-08-15,
ferry-sync 0.3.0 / rclone 1.75.0:

  bulk       nmo-ema-em/ (110 files, 7.1 GiB), transfers=16:
             37 s, ~195 MiB/s sustained, ~100 MB peak RSS.
  resume     kill -9 at 10 s (4.4 GiB landed) -> re-pull completed;
             86 finished files checksum-skipped, 24 in-flight files
             restarted; `rclone check`: 0 differences on all 110 files.
             NB resume is FILE-granular: a partial file restarts from zero.
             Fine for sharded weights; pathological for one giant file.
  manyfiles  sleeper-scaling-sweep/ (52,537 objects, 5.0 GiB),
             transfers=32 checkers=64: 6 m 47 s (~130 objects/s) —
             request-rate-bound, not bandwidth-bound. Tar very-many-tiny-file
             datasets before upload.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import ferry
from ferry.core import _normalize_endpoint, _rclone_bin

DEFAULT_BULK = "gs://alignment-team-general-storage/daniel/jarvis/experiments/nmo-ema-em/"
DEFAULT_MANYFILES = "gs://alignment-team-general-storage/daniel/jarvis/experiments/sleeper-scaling-sweep/"


def _gib(n: int) -> float:
    return n / (1 << 30)


def _fresh(workdir: Path, name: str) -> Path:
    dest = workdir / name
    shutil.rmtree(dest, ignore_errors=True)
    return dest


def _preflight(prefix: str) -> dict:
    info = ferry.size(prefix)
    print(f"  corpus {prefix}: {info['count']} objects, {_gib(info['bytes']):.1f} GiB")
    return info


def tier_bulk(prefix: str, workdir: Path, transfers: int) -> None:
    print(f"\n=== bulk: throughput (transfers={transfers}) ===")
    info = _preflight(prefix)
    dest = _fresh(workdir, "bulk")
    t0 = time.monotonic()
    ferry.pull(prefix, dest, transfers=transfers, progress=False)
    dt = time.monotonic() - t0
    got = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    assert got == info["bytes"], f"byte mismatch: {got} != {info['bytes']}"
    print(f"  PASS: {_gib(got):.2f} GiB in {dt:.0f}s = {got / dt / (1 << 20):.0f} MiB/s")


def tier_resume(prefix: str, workdir: Path, transfers: int, kill_after: float) -> None:
    print(f"\n=== resume: kill -9 at {kill_after:.0f}s, re-pull, verify ===")
    _preflight(prefix)
    dest = _fresh(workdir, "resume")

    # ferry has no kill switch (nor should it) — drive rclone the same way
    # ferry.pull does, but as a Popen we can kill mid-flight.
    cmd = [_rclone_bin(), "copy", _normalize_endpoint(prefix), str(dest),
           "--transfers", str(transfers)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(kill_after)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    partial = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"  killed with {_gib(partial):.1f} GiB landed; re-pulling...")

    t0 = time.monotonic()
    ferry.pull(prefix, dest, transfers=transfers, progress=False)
    dt = time.monotonic() - t0

    check = subprocess.run(
        [_rclone_bin(), "check", str(dest), _normalize_endpoint(prefix)],
        capture_output=True, text=True,
    )
    tail = check.stderr.strip().splitlines()[-1] if check.stderr.strip() else ""
    assert check.returncode == 0, f"rclone check found differences: {tail}"
    print(f"  PASS: re-pull completed in {dt:.0f}s; rclone check: {tail}")


def tier_manyfiles(prefix: str, workdir: Path, transfers: int, checkers: int) -> None:
    print(f"\n=== manyfiles: request-rate regime (transfers={transfers}, checkers={checkers}) ===")
    info = _preflight(prefix)
    dest = _fresh(workdir, "manyfiles")
    t0 = time.monotonic()
    ferry.pull(prefix, dest, transfers=transfers, checkers=checkers, progress=False)
    dt = time.monotonic() - t0
    n = sum(1 for f in dest.rglob("*") if f.is_file())
    assert n == info["count"], f"file count mismatch: {n} != {info['count']}"
    print(f"  PASS: {n} objects in {dt:.0f}s = {n / dt:.0f} objects/s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", type=Path, required=True,
                    help="scratch dir for pulled data (needs corpus-sized free space; wiped per tier)")
    ap.add_argument("--bulk-prefix", default=DEFAULT_BULK)
    ap.add_argument("--manyfiles-prefix", default=DEFAULT_MANYFILES)
    ap.add_argument("--tiers", default="bulk,resume,manyfiles")
    ap.add_argument("--transfers", type=int, default=16)
    ap.add_argument("--manyfiles-transfers", type=int, default=32)
    ap.add_argument("--checkers", type=int, default=64)
    ap.add_argument("--kill-after", type=float, default=10.0)
    ap.add_argument("--keep", action="store_true", help="keep pulled data (default: wipe workdir at exit)")
    args = ap.parse_args()

    ferry.ensure_rclone()
    args.workdir.mkdir(parents=True, exist_ok=True)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    try:
        if "bulk" in tiers:
            tier_bulk(args.bulk_prefix, args.workdir, args.transfers)
        if "resume" in tiers:
            tier_resume(args.bulk_prefix, args.workdir, args.transfers, args.kill_after)
        if "manyfiles" in tiers:
            tier_manyfiles(args.manyfiles_prefix, args.workdir, args.manyfiles_transfers, args.checkers)
    finally:
        if not args.keep:
            shutil.rmtree(args.workdir, ignore_errors=True)
    print("\nall requested tiers passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
