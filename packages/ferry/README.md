# ferry

[![PyPI](https://img.shields.io/pypi/v/ferry-sync?color=blue)](https://pypi.org/project/ferry-sync/)
[![Python](https://img.shields.io/pypi/pyversions/ferry-sync)](https://pypi.org/project/ferry-sync/)
[![CI](https://github.com/dtch1997/arsenal/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dtch1997/arsenal/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Pythonic `push` / `pull` between your machine and any storage backend —
GCS, S3, Drive, SFTP, … — in a couple of lines. Zero config for GCS and S3.

```python
import ferry

ferry.pull("gs://my-bucket/weights/llama-405b/", "/workspace/weights/")
ferry.push("results/", "s3://my-bucket/exp/results/")
```

ferry is a **thin wrapper around [rclone](https://rclone.org)**. It does not
move bytes itself: rclone already does diffing, parallelism, multipart, resume,
and dozens of backends, far better than a hand-rolled client would. ferry only
adds the ergonomic, convention-aware Python surface you actually want to call.

## Install

```bash
pip install ferry-sync    # import name: ferry
```

ferry needs the `rclone` binary at runtime. If it's missing, ferry can fetch
the static build itself (into `~/.local/bin`, no sudo):

```bash
ferry install-rclone      # or in Python: ferry.ensure_rclone()
```

## Endpoints

An endpoint is a plain string, in one of three forms:

- **local path** — `results/`.
- **cloud URL** — `gs://bucket/key` or `s3://bucket/key`. **No `rclone config`
  needed**: ferry maps these to on-the-fly rclone backends that authenticate
  from the environment — Application Default Credentials or
  `GOOGLE_APPLICATION_CREDENTIALS` for GCS; `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` for S3.
- **rclone endpoint** — `name:bucket/key` where `name` comes from
  `rclone listremotes`, for anything you've set up with `rclone config`
  (Drive, SFTP, R2, …).

## Fresh pod → weights, in three lines

The whole point for ephemeral compute (RunPod, Modal, CI): nothing to
configure interactively, credentials come in via env.

```python
import ferry

ferry.ensure_rclone()                                     # no-op if installed
ferry.pull("gs://my-bucket/weights/big-model/", "/workspace/weights/",
           transfers=16)
```

Credentials-wise you have two options. With a scoped service-account key,
export `GOOGLE_APPLICATION_CREDENTIALS` on the pod and `gs://` URLs just
work. Without one, `ferry.gcs_pod_env()` — run on your *local* machine —
mints a short-lived (~1 h) access token from your gcloud ADC and returns env
vars that define rclone remote `gcs:` wherever they're exported (e.g.
bellhop's `RunSpec(env=ferry.gcs_pod_env())`); the pod then uses
`gcs:bucket/path` endpoints. No long-lived secret leaves your machine and
access self-expires; the flip side is the token can't refresh, so it only
fits jobs that finish (or at least finish their transfers) within the hour.

Preflight before committing to a multi-hundred-GB transfer:

```bash
ferry doctor gs://my-bucket/weights/big-model/   # binary? creds? endpoint listable?
ferry size   gs://my-bucket/weights/big-model/   # how much am I about to pull?
```

## Semantics

- **Additive by default.** `push`/`pull` run `rclone copy`: nothing on the
  destination is deleted. Interrupted transfers are safe to re-run — rclone
  skips what's already there and picks up the rest.
- **`mirror=True`** runs `rclone sync` instead — the destination becomes an
  exact mirror, which **deletes** files on the destination that are absent at
  the source. Use with care; pair with `dry_run=True` first.

```python
ferry.push("results/", "gs://bkt/exp/", dry_run=True)               # preview
ferry.push("results/", "gs://bkt/exp/", mirror=True)                # exact mirror
ferry.push("results/", "gs://bkt/exp/", excludes=["*.tmp", ".git/**"])
ferry.push("results/", "gs://bkt/exp/", transfers=16, checkers=32)  # parallelism
```

Any extra rclone flags pass straight through:

```python
ferry.pull("gs://bkt/x/", "x/", flags=["--checksum", "--fast-list"])
```

### Big-transfer knobs (200 GB and up)

rclone's defaults are already resume-safe and multipart; the knobs that matter
at weight-scale are parallelism:

```python
ferry.pull("gs://bkt/weights/", "w/",
           transfers=16,                            # parallel files
           flags=["--multi-thread-streams", "8"])   # parallel chunks per big file
```

For S3, `--s3-chunk-size 128M` raises multipart chunk size (fewer requests);
`--fast-list` speeds up listing prefixes with many objects.

## Helpers

```python
ferry.ls("gs://bkt/exp/")       # entries at top level, dirs suffixed "/"
ferry.size("gs://bkt/exp/")     # {"count": 1234, "bytes": 217871234567}
ferry.listremotes()             # names from rclone config
ferry.ensure_rclone()           # path to rclone, downloading it if absent
```

## Bound remotes

`Remote` fixes a base prefix so calls take a relative path mapped under it —
the "I never want to retype the bucket" ergonomic:

```python
exp = ferry.Remote(
    "gs://my-bucket/experiments/foo",
    defaults={"excludes": ["*.tmp"], "transfers": 16},   # applied to every call
)
exp.push("results/")          # -> gs://my-bucket/experiments/foo/results/
exp.pull("checkpoints/")      # <- gs://my-bucket/experiments/foo/checkpoints/
exp.child("logs").push("./")  # -> gs://my-bucket/experiments/foo/logs/
exp.ls()                      # entries under the base
exp.size()                    # totals under the base
```

## CLI

The same thing from the shell:

```bash
ferry pull gs://bkt/exp/ results/
ferry push results/ gs://bkt/exp/ --transfers 16
ferry pull gs://bkt/exp/ results/ --mirror --dry-run
ferry ls gs://bkt/exp/
ferry size gs://bkt/exp/
ferry doctor [gs://bkt/exp/]
ferry install-rclone
ferry remotes
```

## Stress testing

`scripts/stress.py` is a repeatable three-tier stress suite (bulk throughput,
kill-9 + resume with byte-level `rclone check`, many-small-files) run against
gs:// corpora you point it at. Reference numbers from the 2026-08-15 devbox
pass are in its docstring — headline: ~195 MiB/s sustained, kill-resume left
0 byte differences across 110 files, and resume is **file-granular** (a
partial file restarts from zero — fine for sharded weights, slow for one
giant file). Tiny-file trees are request-rate-bound (~130 objects/s): tar
them before upload.

`scripts/stress_pod.py` is the pod-tier version — the same suite on a fresh
RunPod pod via bellhop, PyPI install, credentials via `gcs_pod_env()`. The
2026-08-15 pass (RTX 4090, COMMUNITY cloud): bulk 7.1 GiB at ~57 MiB/s,
kill-resume 110/110 files matching, clean teardown. At community-pod network
speed a 200 GB pull is ~1 h — brushing the token TTL, so prefer a
service-account key for the largest jobs.

## What ferry deliberately is not

- Not a new transfer engine — that's rclone's job.
- Not a credential manager — creds come from the environment (ADC, `AWS_*`),
  exactly as your cloud SDKs already expect.
- Not a daemon / continuous watcher — it's one-shot `push`/`pull` you call.

## Content-addressed store — `ferry.cas`

Absorbed from the retired [cloudfs](https://github.com/ArcadiaImpact/cloudfs)
library: a minimal content-addressed file store on GCS. Files are keyed by
the MD5 of their content — uploads are idempotent, identical content is
stored once. Needs the extra: `pip install "ferry-sync[gcs]"` (auth via
Application Default Credentials, like everything else).

```python
from ferry import cas

file_id = cas.upload("model.safetensors")   # -> md5 hex id
cas.download(file_id, "restored.safetensors")
cas.uri(file_id)                            # gs://<bucket>/<prefix>/<id>
```

Bucket/prefix/project: `FERRY_CAS_BUCKET` / `FERRY_CAS_PREFIX` /
`FERRY_CAS_PROJECT` (legacy `CLOUDFS_*` still honored), defaulting to the
same bucket+prefix cloudfs used, so existing ids keep resolving. CLI:
`ferry cas upload|download|exists|rm|uri`.

Rule of thumb: moving an experiment *tree* by path → `ferry.push/pull`;
storing/serving a single artifact by *content hash* (dedup, stable ids) →
`ferry.cas`.

## Changes in 0.3.0

- `gs://` / `s3://` URLs accepted everywhere — zero-config, env-authenticated.
- `ferry.ensure_rclone()` / `ferry install-rclone` — bootstrap the binary.
- `ferry doctor [endpoint]` — preflight binary, creds, and endpoint access.
- `ferry.ls` / `ferry.size` (+ CLI `ls`/`size`, + `Remote.size`).
- **Breaking:** `Remote.ls()` now returns `list[str]` (was a raw string).
