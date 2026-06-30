# ferry

Pythonic `push` / `pull` between your local machine and any storage backend —
GCS, S3, Drive, SFTP, … — in a couple of lines.

ferry is a **thin wrapper around [rclone](https://rclone.org)**. It does not
move bytes itself: rclone already does diffing, parallelism, resume, and dozens
of backends, far better than a hand-rolled client would. ferry only adds the
ergonomic, convention-aware Python surface you actually want to call.

```python
import ferry

# explicit endpoints (rclone syntax: "remote:bucket/prefix")
ferry.push("results/", "gcs:my-bucket/exp/results/")   # local -> remote
ferry.pull("gcs:my-bucket/exp/results/", "results/")   # remote -> local

# bound remote — base is implicit, directory structure preserved
exp = ferry.Remote("gcs:my-bucket/experiments/foo")
exp.push("results/")   # ./results/   -> gcs:my-bucket/experiments/foo/results/
exp.pull("results/")   # gcs:.../results/ -> ./results/
```

## Install

```bash
pip install git+https://github.com/dtch1997/ferry   # the library
```

ferry needs the `rclone` binary on PATH at runtime:

```bash
curl https://rclone.org/install.sh | sudo bash   # or brew install rclone
rclone config                                     # set up a remote, e.g. "gcs"
```

A *local* endpoint is a plain path (`results/`). A *remote* endpoint is
`name:bucket/key`, where `name` comes from `rclone listremotes`.

## Semantics

- **Additive by default.** `push`/`pull` run `rclone copy`: nothing on the
  destination is deleted. Safe.
- **`mirror=True`** runs `rclone sync` instead — the destination becomes an
  exact mirror, which **deletes** files on the destination that are absent at
  the source. Use with care; pair with `dry_run=True` first.

```python
ferry.push("results/", "gcs:bkt/exp/", dry_run=True)              # preview
ferry.push("results/", "gcs:bkt/exp/", mirror=True)              # exact mirror
ferry.push("results/", "gcs:bkt/exp/", excludes=["*.tmp", ".git/**"])
ferry.push("results/", "gcs:bkt/exp/", transfers=16, checkers=32)  # parallelism
```

Any extra rclone flags pass straight through:

```python
ferry.pull("gcs:bkt/x/", "x/", flags=["--checksum", "--fast-list"])
```

## Bound remotes

`Remote` fixes a base prefix so calls take a relative path mapped under it —
the "I never want to retype the bucket" ergonomic:

```python
exp = ferry.Remote(
    "gcs:my-bucket/experiments/foo",
    defaults={"excludes": ["*.tmp"], "transfers": 16},   # applied to every call
)
exp.push("results/")          # -> gcs:my-bucket/experiments/foo/results/
exp.pull("checkpoints/")      # <- gcs:my-bucket/experiments/foo/checkpoints/
exp.child("logs").push("./") # -> gcs:my-bucket/experiments/foo/logs/
print(exp.ls())               # rclone lsf listing of the base
```

## CLI

The same thing from the shell:

```bash
ferry push results/ gcs:bkt/exp/
ferry pull gcs:bkt/exp/ results/ --mirror --dry-run
ferry push d/ gcs:bkt/d/ --exclude '*.tmp' --transfers 16
ferry remotes
```

## What ferry deliberately is not

- Not a new transfer engine — that's rclone's job.
- Not content-addressed storage — see `cloudfs` for MD5-keyed blob storage.
- Not a daemon / continuous watcher — it's one-shot `push`/`pull` you call.
