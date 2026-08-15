"""Pythonic push/pull over rclone.

A thin, honest wrapper. ``ferry`` does not move bytes itself — it shells out to
``rclone``, which already handles diffing, parallelism, resume, and dozens of
backends. ferry only adds an ergonomic, convention-aware Python surface:

    import ferry

    # cloud URLs — zero config, auth comes from the environment
    ferry.pull("gs://my-bucket/weights/", "/workspace/weights/")
    ferry.push("results/", "s3://my-bucket/exp/results/")

    # or explicit rclone endpoints ("remote:bucket/prefix" from `rclone config`)
    ferry.push("results/", "gcs:my-bucket/exp/results/")

    # bound remote — the remote base is implicit, structure preserved
    exp = ferry.Remote("gs://my-bucket/experiments/foo")
    exp.push("results/")   # ./results/ -> gs://my-bucket/experiments/foo/results/
    exp.pull("results/")   # gs://my-bucket/experiments/foo/results/ -> ./results/

Endpoints are plain strings. Three forms are understood:

- a *local* path: no ``remote:`` prefix (``results/``);
- a *cloud URL*: ``gs://bucket/key`` or ``s3://bucket/key`` — translated to an
  on-the-fly rclone backend that authenticates from the environment
  (Application Default Credentials / ``GOOGLE_APPLICATION_CREDENTIALS`` for
  GCS; ``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``/``AWS_REGION`` for S3),
  so no ``rclone config`` step is ever needed;
- an *rclone endpoint*: ``remote:bucket/key`` where ``remote`` is a name from
  ``rclone listremotes`` / ``rclone config``.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


class RcloneNotFound(RuntimeError):
    """Raised when the ``rclone`` binary is not on PATH."""


class RcloneError(subprocess.CalledProcessError):
    """Raised when an rclone invocation exits non-zero."""


# Cloud-URL schemes -> on-the-fly rclone backends. ``env_auth=true`` makes
# rclone pick up credentials from the environment (ADC / AWS env vars), which
# is what a fresh pod or CI box actually has — no `rclone config` required.
# ``bucket_policy_only=true``: uniform-bucket-level-access buckets reject the
# legacy object ACL rclone otherwise sends (googleapi 400); with it rclone
# skips ACLs and objects inherit bucket policy, which is also fine for
# fine-grained-ACL buckets.
_URL_SCHEMES = {
    "gs": ":gcs,env_auth=true,bucket_policy_only=true:",
    "s3": ":s3,env_auth=true:",
}

_LOCAL_BIN = Path.home() / ".local" / "bin"


def _normalize_endpoint(endpoint: str) -> str:
    """Translate ``gs://`` / ``s3://`` URLs to on-the-fly rclone backends.

    Local paths and ``remote:path`` endpoints pass through untouched.
    """
    for scheme, backend in _URL_SCHEMES.items():
        prefix = scheme + "://"
        if endpoint.startswith(prefix):
            return backend + endpoint[len(prefix):]
    return endpoint


def _rclone_bin() -> str:
    override = os.environ.get("FERRY_RCLONE")
    if override:
        return override
    binary = shutil.which("rclone")
    if binary is not None:
        return binary
    fallback = _LOCAL_BIN / "rclone"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise RcloneNotFound(
        "rclone is not installed or not on PATH. Run `ferry install-rclone` "
        "(Python: ferry.ensure_rclone()) to fetch the static binary into "
        "~/.local/bin — no sudo needed — or install it yourself: "
        "https://rclone.org/install/"
    )


def ensure_rclone(dest_dir: str | Path | None = None) -> str:
    """Return a path to an rclone binary, downloading one if none is found.

    Checks ``$FERRY_RCLONE``, PATH, and ``~/.local/bin`` first; otherwise
    downloads the current static build from downloads.rclone.org into
    ``dest_dir`` (default ``~/.local/bin``, no sudo needed). Idempotent —
    safe to call at the top of any script that may run on a fresh pod.
    """
    try:
        return _rclone_bin()
    except RcloneNotFound:
        pass

    osname = {"linux": "linux", "darwin": "osx"}.get(platform.system().lower())
    arch = {
        "x86_64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(platform.machine().lower())
    if osname is None or arch is None:
        raise RcloneNotFound(
            f"no static rclone build for {platform.system()}/{platform.machine()}; "
            "install manually: https://rclone.org/install/"
        )

    url = f"https://downloads.rclone.org/rclone-current-{osname}-{arch}.zip"
    with urllib.request.urlopen(url) as resp:
        payload = resp.read()

    dest_dir = Path(dest_dir) if dest_dir is not None else _LOCAL_BIN
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "rclone"
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        member = next(n for n in zf.namelist() if n.endswith("/rclone"))
        dest.write_bytes(zf.read(member))
    dest.chmod(0o755)
    return str(dest)


def _is_remote(endpoint: str) -> bool:
    """True if ``endpoint`` is remote (cloud URL, ``remote:path``, or an
    on-the-fly ``:backend:path``), not a local path.

    Windows drive letters ("C:\\foo") are treated as local: a remote uses a
    multi-char name, a drive is a single letter.
    """
    if endpoint.startswith(":"):
        return True  # on-the-fly backend, ":gcs,env_auth=true:bucket"
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
    args: list[str] = [verb, _normalize_endpoint(src), _normalize_endpoint(dst)]
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


def ls(endpoint: str, **kw) -> list[str]:
    """Entries directly under ``endpoint`` (``rclone lsf``), dirs suffixed "/"."""
    out = _run(
        ["lsf", _normalize_endpoint(endpoint)], progress=False, capture=True, **kw
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def size(endpoint: str, **kw) -> dict:
    """Object count and total bytes under ``endpoint``.

    Returns ``rclone size --json`` output, e.g. ``{"count": 12, "bytes": 123}``.
    Useful as a preflight before committing to a multi-hundred-GB pull.
    """
    out = _run(
        ["size", "--json", _normalize_endpoint(endpoint)],
        progress=False, capture=True, **kw,
    ).stdout
    return json.loads(out)


def _join_remote(base: str, sub: str) -> str:
    if not sub or sub in (".", "./"):
        return base
    return base.rstrip("/") + "/" + sub.lstrip("/")


@dataclass
class Remote:
    """A bound remote base. ``push``/``pull`` take a relative path and map it
    under ``base`` on the remote, preserving directory structure.

        exp = Remote("gs://my-bucket/experiments/foo")
        exp.push("results/")   # ./results/ -> gs://my-bucket/experiments/foo/results/

    ``base`` may be a cloud URL (``gs://…``, ``s3://…``) or a configured
    rclone endpoint (``gcs:…``). ``defaults`` are keyword args applied to
    every transfer (e.g. ``excludes=["*.tmp"]``); per-call kwargs override
    them.
    """

    base: str
    defaults: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_remote(self.base):
            raise ValueError(
                f"Remote base {self.base!r} is not a remote endpoint "
                '(expected "gs://bucket/prefix", "s3://bucket/prefix", or '
                'an rclone "remote:bucket/prefix").'
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

    def ls(self, sub: str = "", **kw) -> list[str]:
        """Entries under ``base/sub`` (``rclone lsf``), dirs suffixed "/"."""
        target = _join_remote(self.base, sub) if sub else self.base
        return ls(target, **kw)

    def size(self, sub: str = "", **kw) -> dict:
        """Object count and total bytes under ``base/sub`` (see :func:`size`)."""
        target = _join_remote(self.base, sub) if sub else self.base
        return size(target, **kw)


def listremotes() -> list[str]:
    """Names of configured rclone remotes (without the trailing colon)."""
    out = _run(["listremotes"], progress=False, capture=True).stdout
    return [line.rstrip(":") for line in out.splitlines() if line.strip()]
