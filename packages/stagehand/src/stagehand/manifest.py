"""manifest — automatic provenance: which code, invocation, and config produced
a run (and its artifacts).

Answers the question every result file eventually gets asked — "which commit
produced this?" — without the caller doing anything. `capture()` snapshots the
git state (sha / dirty / branch / remote), the exact invocation (argv, cwd,
python, host), a timestamp, and an optional user config; `write()` puts that
next to the results as `manifest.json`.

Wired in automatically:

  - `Flow.run()` writes `runs_dir/manifest.json` at start (when `runs_dir` is
    set), including the flow's title; pass `Flow(..., config=cfg)` to snapshot
    your experiment config into it.
  - `ArtifactStore.put()` stamps `{"sha", "dirty"}` into every produced
    artifact's `meta["git"]`, so a committed lineage lock-file records the code
    version alongside `inputs`/`produced_by`.

Git lookups shell out once per process per directory (cached); outside a git
repo everything degrades to `git: null` rather than failing — a manifest with
gaps still beats no manifest.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git(*args, cwd) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except OSError:
        return None


_git_cache: dict[str, dict | None] = {}


def git_info(cwd=None) -> dict | None:
    """Git provenance for the repo containing `cwd` (default: the process cwd):
    `{sha, dirty, branch, remote}` — or None outside a repo. Cached per
    directory for the life of the process, so stamping many artifacts is cheap;
    `dirty` therefore reflects the state at *first* capture."""
    key = str(Path(cwd or os.getcwd()).resolve())
    if key not in _git_cache:
        sha = _git("rev-parse", "HEAD", cwd=key)
        _git_cache[key] = None if sha is None else {
            "sha": sha,
            "dirty": bool(_git("status", "--porcelain", cwd=key)),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=key),
            "remote": _git("remote", "get-url", "origin", cwd=key),
        }
    return _git_cache[key]


def git_stamp(cwd=None) -> dict | None:
    """The compact per-artifact stamp: `{"sha", "dirty"}` (None outside a repo)."""
    g = git_info(cwd)
    return None if g is None else {"sha": g["sha"], "dirty": g["dirty"]}


def capture(config=None, **extra) -> dict:
    """Snapshot provenance for the current process: timestamp, git state, argv,
    cwd, python version, hostname — plus `config` (any JSON-serializable value;
    your experiment's resolved config dict belongs here) and any `extra` keys."""
    m = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_info(),
        "argv": list(sys.argv),
        "cwd": os.getcwd(),
        "python": sys.version.split()[0],
        "host": socket.gethostname(),
    }
    if config is not None:
        m["config"] = config
    m.update(extra)
    return m


def write_manifest(path, config=None, **extra) -> dict:
    """`capture()` and write it to `path` as JSON; returns the manifest dict."""
    m = capture(config, **extra)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, indent=2, sort_keys=True, default=str))
    return m
