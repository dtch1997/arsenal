"""Pythonic push/pull over rclone.

A thin, honest wrapper. ``ferry`` does not move bytes itself — it shells out to
``rclone``, which already handles diffing, parallelism, resume, and dozens of
backends. ferry only adds an ergonomic, convention-aware Python surface:

    import ferry

    # explicit endpoints (rclone path syntax: "remote:bucket/prefix")
    ferry.push("results/", "gcs:my-bucket/exp/results/")   # local -> remote
    ferry.pull("gcs:my-bucket/exp/results/", "results/")   # remote -> local

    # bound remote — the remote base is implicit, structure preserved
    exp = ferry.Remote("gcs:my-bucket/experiments/foo")
    exp.push("results/")   # ./results/ -> gcs:my-bucket/experiments/foo/results/
    exp.pull("results/")   # gcs:my-bucket/experiments/foo/results/ -> ./results/

Endpoints are plain strings in rclone syntax. A *local* path has no ``remote:``
prefix; a *remote* path looks like ``remote:bucket/key`` where ``remote`` is a
name from ``rclone listremotes`` / ``rclone config``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


class RcloneNotFound(RuntimeError):
    """Raised when the ``rclone`` binary is not on PATH."""


class RcloneError(subprocess.CalledProcessError):
    """Raised when an rclone invocation exits non-zero."""


def _rclone_bin() -> str:
    binary = shutil.which("rclone")
    if binary is None:
        raise RcloneNotFound(
            "rclone is not installed or not on PATH. ferry is a thin wrapper "
            "around rclone — install it (https://rclone.org/install/) and "
            "configure a remote with `rclone config`."
        )
    return binary


def _is_remote(endpoint: str) -> bool:
    """True if ``endpoint`` is an rclone remote ("remote:path"), not a local path.

    Windows drive letters ("C:\\foo") are treated as local: a remote uses a
    multi-char name, a drive is a single letter.
    """
    head, sep, _ = endpoint.partition(":")
    if not sep:
        return False
    return len(head) > 1


@dataclass
class RcloneResult:
    """Outcome of an rclone run."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(
    rclone_args: Sequence[str],
    *,
    dry_run: bool = False,
    progress: bool = True,
    transfers: int | None = None,
    checkers: int | None = None,
    flags: Sequence[str] = (),
    check: bool = True,
    capture: bool = False,
) -> RcloneResult:
    cmd = [_rclone_bin(), *rclone_args]
    if dry_run:
        cmd.append("--dry-run")
    if progress and not capture:
        cmd.append("--progress")
    if transfers is not None:
        cmd += ["--transfers", str(transfers)]
    if checkers is not None:
        cmd += ["--checkers", str(checkers)]
    cmd += list(flags)

    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=capture,
    )
    result = RcloneResult(
        args=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout or "" if capture else "",
        stderr=proc.stderr or "" if capture else "",
    )
    if check and proc.returncode != 0:
        raise RcloneError(proc.returncode, cmd, result.stdout, result.stderr)
    return result


def _transfer(
    src: str,
    dst: str,
    *,
    mirror: bool,
    excludes: Sequence[str],
    includes: Sequence[str],
    **kw,
) -> RcloneResult:
    verb = "sync" if mirror else "copy"
    args: list[str] = [verb, src, dst]
    for pattern in includes:
        args += ["--include", pattern]
    for pattern in excludes:
        args += ["--exclude", pattern]
    return _run(args, **kw)


def push(
    local: str | Path,
    remote: str,
    *,
    mirror: bool = False,
    dry_run: bool = False,
    excludes: Sequence[str] = (),
    includes: Sequence[str] = (),
    progress: bool = True,
    transfers: int | None = None,
    checkers: int | None = None,
    flags: Sequence[str] = (),
    capture: bool = False,
) -> RcloneResult:
    """Upload ``local`` to ``remote``.

    By default this is additive (``rclone copy``): files on the remote that are
    not present locally are left untouched. Pass ``mirror=True`` to make the
    remote an exact mirror of local (``rclone sync`` — this DELETES remote files
    that don't exist locally).
    """
    return _transfer(
        str(local),
        remote,
        mirror=mirror,
        excludes=excludes,
        includes=includes,
        dry_run=dry_run,
        progress=progress,
        transfers=transfers,
        checkers=checkers,
        flags=flags,
        capture=capture,
    )


def pull(
    remote: str,
    local: str | Path,
    *,
    mirror: bool = False,
    dry_run: bool = False,
    excludes: Sequence[str] = (),
    includes: Sequence[str] = (),
    progress: bool = True,
    transfers: int | None = None,
    checkers: int | None = None,
    flags: Sequence[str] = (),
    capture: bool = False,
) -> RcloneResult:
    """Download ``remote`` to ``local``.

    Additive by default (``rclone copy``). ``mirror=True`` makes the local dir an
    exact mirror (``rclone sync`` — DELETES local files absent on the remote).
    """
    return _transfer(
        remote,
        str(local),
        mirror=mirror,
        excludes=excludes,
        includes=includes,
        dry_run=dry_run,
        progress=progress,
        transfers=transfers,
        checkers=checkers,
        flags=flags,
        capture=capture,
    )


def _join_remote(base: str, sub: str) -> str:
    if not sub or sub in (".", "./"):
        return base
    return base.rstrip("/") + "/" + sub.lstrip("/")


@dataclass
class Remote:
    """A bound remote base. ``push``/``pull`` take a relative path and map it
    under ``base`` on the remote, preserving directory structure.

        exp = Remote("gcs:my-bucket/experiments/foo")
        exp.push("results/")   # ./results/ -> gcs:my-bucket/experiments/foo/results/

    ``defaults`` are keyword args applied to every transfer (e.g.
    ``excludes=["*.tmp"]``); per-call kwargs override them.
    """

    base: str
    defaults: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_remote(self.base):
            raise ValueError(
                f"Remote base {self.base!r} is not an rclone remote "
                '(expected "remote:bucket/prefix").'
            )

    def _merge(self, kw: dict) -> dict:
        merged = dict(self.defaults)
        merged.update(kw)
        return merged

    def child(self, sub: str) -> "Remote":
        """A sub-remote rooted at ``base/sub`` (inherits defaults)."""
        return Remote(_join_remote(self.base, sub), defaults=dict(self.defaults))

    def push(self, path: str | Path = ".", remote_subpath: str | None = None, **kw) -> RcloneResult:
        local = Path(path)
        sub = remote_subpath if remote_subpath is not None else local.name
        dst = _join_remote(self.base, sub) if sub not in ("", ".") else self.base
        return push(local, dst, **self._merge(kw))

    def pull(self, path: str | Path = ".", remote_subpath: str | None = None, **kw) -> RcloneResult:
        local = Path(path)
        sub = remote_subpath if remote_subpath is not None else local.name
        src = _join_remote(self.base, sub) if sub not in ("", ".") else self.base
        return pull(src, local, **self._merge(kw))

    def ls(self, sub: str = "", **kw) -> str:
        """Return ``rclone lsf`` listing of ``base/sub`` (one name per line)."""
        target = _join_remote(self.base, sub) if sub else self.base
        return _run(["lsf", target], progress=False, capture=True, **kw).stdout


def listremotes() -> list[str]:
    """Names of configured rclone remotes (without the trailing colon)."""
    out = _run(["listremotes"], progress=False, capture=True).stdout
    return [line.rstrip(":") for line in out.splitlines() if line.strip()]
