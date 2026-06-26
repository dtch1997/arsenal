"""The Pod resource: provision, wait-until-functional, exec / push / pull.

This is the composable layer the bash driver can't offer — keep a pod alive and
run many steps against it. ``run()`` (see run.py) is just this plus GCS upload.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import AsyncIterator, Literal

from .errors import PodNotReadyError, PreflightError, ProvisionError
from .probes import ReadyProbe, SshProbe
from .rest import RunpodRest

# Image presets — kept inline so the library is standalone (no jarvis catalog).
IMAGE_PRESETS = {
    "cpu-base": "runpod/base:1.0.2-ubuntu2204",
    "pytorch-cuda": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
    "pytorch-latest": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
}
DEFAULT_GPU_IMAGE = IMAGE_PRESETS["pytorch-cuda"]
DEFAULT_CPU_IMAGE = IMAGE_PRESETS["cpu-base"]

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=30",
]

TAR_EXCLUDES = ["--exclude=.git", "--exclude=__pycache__", "--exclude=.venv",
                "--exclude=node_modules", "--exclude=*.pyc"]


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class PodConfig:
    compute: Literal["cpu", "gpu"] = "gpu"
    gpu_id: str | None = None              # required when compute="gpu"
    gpu_count: int = 1
    image: str | None = None               # free-form; wins over preset
    image_preset: str | None = None        # key into IMAGE_PRESETS
    container_disk_gb: int = 20
    volume_gb: int | None = None           # network-volume persistence
    volume_mount_path: str = "/workspace"
    cloud: Literal["SECURE", "COMMUNITY"] = "COMMUNITY"
    cloud_fallback: bool = True            # COMMUNITY out-of-stock -> retry SECURE
    ports: list[str] = field(default_factory=lambda: ["22/tcp"])
    env: dict[str, str] = field(default_factory=dict)
    name: str = "runpod-runner"
    # auth / connection
    ssh_key: str | None = None             # private key; default ~/.ssh/id_ed25519
    ssh_user: str = "root"
    # readiness
    ready: ReadyProbe = field(default_factory=lambda: SshProbe("true"))
    provision_timeout: timedelta = timedelta(seconds=300)
    ready_timeout: timedelta = timedelta(seconds=420)
    poll_interval: float = 8.0

    def resolve_image(self) -> str:
        if self.image:
            return self.image
        if self.image_preset:
            try:
                return IMAGE_PRESETS[self.image_preset]
            except KeyError:
                raise PreflightError(
                    f"unknown image_preset {self.image_preset!r} (have {list(IMAGE_PRESETS)})"
                )
        return DEFAULT_GPU_IMAGE if self.compute == "gpu" else DEFAULT_CPU_IMAGE

    def resolve_ssh_key(self) -> str:
        key = self.ssh_key or os.path.expanduser("~/.ssh/id_ed25519")
        if not Path(key).exists():
            raise PreflightError(f"ssh private key not found: {key}")
        return key

    def pubkey_text(self) -> str:
        pub = self.resolve_ssh_key() + ".pub"
        if not Path(pub).exists():
            raise PreflightError(f"ssh public key not found: {pub}")
        return Path(pub).read_text().strip()

    def to_create_body(self) -> dict:
        if self.compute == "gpu" and not self.gpu_id:
            raise PreflightError("gpu_id required when compute='gpu'")
        env = dict(self.env)
        env.setdefault("PUBLIC_KEY", self.pubkey_text())  # RunPod injects into authorized_keys
        body: dict = {
            "name": self.name,
            "imageName": self.resolve_image(),
            "cloudType": self.cloud,
            "containerDiskInGb": self.container_disk_gb,
            "ports": self.ports,
            "env": env,
        }
        if self.compute == "gpu":
            body["gpuTypeIds"] = [self.gpu_id]
            body["gpuCount"] = self.gpu_count
        else:
            body["computeType"] = "CPU"
        if self.volume_gb:
            body["volumeInGb"] = self.volume_gb
            body["volumeMountPath"] = self.volume_mount_path
        return body


class Pod:
    """A live pod. Construct via :func:`pod` (the async context manager)."""

    def __init__(self, rest: RunpodRest, pod_id: str, config: PodConfig):
        self._rest = rest
        self.id = pod_id
        self.config = config
        self._meta: dict = {}
        self._ssh_key = config.resolve_ssh_key()

    # ---- connection info ---------------------------------------------------
    @property
    def host(self) -> str | None:
        return self._meta.get("publicIp")

    def mapped_port(self, container_port: int = 22) -> int | None:
        return (self._meta.get("portMappings") or {}).get(str(container_port))

    @property
    def status(self) -> str:
        return (self._meta.get("desiredStatus") or "UNKNOWN").upper()

    def proxy_url(self, container_port: int) -> str:
        return f"https://{self.id}-{container_port}.proxy.runpod.net"

    # ---- lifecycle ---------------------------------------------------------
    async def refresh(self) -> dict:
        self._meta = await self._rest.get_pod(self.id)
        return self._meta

    async def _wait_provision(self) -> None:
        deadline = time.monotonic() + self.config.provision_timeout.total_seconds()
        while True:
            await self.refresh()
            if self.status in ("EXITED", "TERMINATED"):
                raise ProvisionError(f"pod {self.id} entered terminal state {self.status}")
            if self.status == "RUNNING" and self.host and self.mapped_port(22):
                return
            if time.monotonic() >= deadline:
                raise PodNotReadyError(
                    f"pod {self.id} not RUNNING+routable within "
                    f"{self.config.provision_timeout.total_seconds():.0f}s (status={self.status})"
                )
            await asyncio.sleep(self.config.poll_interval)

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.config.ready_timeout.total_seconds()
        while True:
            if await self.config.ready(self):
                return
            if time.monotonic() >= deadline:
                raise PodNotReadyError(
                    f"pod {self.id} provisioned but readiness probe never passed "
                    f"within {self.config.ready_timeout.total_seconds():.0f}s"
                )
            await asyncio.sleep(self.config.poll_interval)

    async def teardown(self) -> None:
        await self._rest.delete_pod(self.id)

    # ---- exec / transfer ---------------------------------------------------
    def _ssh_argv(self) -> list[str]:
        port = self.mapped_port(22)
        if not (self.host and port):
            raise PodNotReadyError("ssh endpoint not available yet")
        return ["ssh", "-i", self._ssh_key, *SSH_OPTS, "-p", str(port),
                f"{self.config.ssh_user}@{self.host}"]

    def _ssh_prefix(self) -> str:
        return " ".join(shlex.quote(a) for a in self._ssh_argv())

    async def _ssh_raw(self, cmd: str, timeout: float = 600) -> ExecResult:
        """Run a single command over ssh (no readiness gating)."""
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_argv(), cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await _communicate(proc, timeout=timeout)
        return ExecResult(proc.returncode or 0, out, err)

    async def exec(self, cmd: str, env: dict[str, str] | None = None,
                   timeout: float = 3600) -> ExecResult:
        """Run command(s) on the pod.

        Env vars are exported *inside* the script (a fresh sshd session does not
        inherit the container's PID-1 env), and the whole script is fed over
        stdin to ``bash -ls`` so secret values never appear in the pod's argv.
        """
        exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in (env or {}).items())
        script = f"set -o pipefail\n{exports}\n{cmd}\n"
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_argv(), "bash", "-ls",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await _communicate(proc, stdin=script.encode(), timeout=timeout)
        return ExecResult(proc.returncode or 0, out, err)

    async def push(self, local: str | Path, remote: str) -> None:
        """Upload a local directory to ``remote`` on the pod (tar-over-ssh)."""
        local = str(local)
        if not Path(local).is_dir():
            raise PreflightError(f"push source not a directory: {local}")
        excl = " ".join(TAR_EXCLUDES)
        remote_cmd = f"mkdir -p {shlex.quote(remote)} && tar xzf - -C {shlex.quote(remote)}"
        pipeline = (
            f"tar czf - -C {shlex.quote(local)} {excl} . "
            f"| {self._ssh_prefix()} {shlex.quote(remote_cmd)}"
        )
        await _run_shell(pipeline, what="push")

    async def pull(self, remote: str, local_dest: str | Path) -> None:
        """Download remote dir into ``local_dest`` (creates local_dest/<basename>)."""
        local_dest = str(local_dest)
        Path(local_dest).mkdir(parents=True, exist_ok=True)
        parent = os.path.dirname(remote.rstrip("/")) or "/"
        base = os.path.basename(remote.rstrip("/"))
        remote_cmd = f"tar czf - -C {shlex.quote(parent)} {shlex.quote(base)}"
        pipeline = (
            f"{self._ssh_prefix()} {shlex.quote(remote_cmd)} "
            f"| tar xzf - -C {shlex.quote(local_dest)}"
        )
        await _run_shell(pipeline, what="pull")

    async def exists_remote(self, path: str) -> bool:
        res = await self._ssh_raw(f"test -e {shlex.quote(path)}")
        return res.exit_code == 0


async def _communicate(proc, stdin: bytes | None = None, timeout: float = 600):
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def _run_shell(pipeline: str, what: str) -> None:
    proc = await asyncio.create_subprocess_shell(
        pipeline, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed (rc={proc.returncode}): {err.decode('utf-8','replace')[:500]}")


@contextlib.asynccontextmanager
async def pod(config: PodConfig, *, keep: bool = False,
              api_key: str | None = None) -> AsyncIterator[Pod]:
    """Provision a pod, wait until it's functional, yield it, tear it down.

    On any exception (including a readiness timeout) the pod is still deleted,
    unless ``keep=True``.
    """
    async with RunpodRest(api_key=api_key) as rest:
        body = config.to_create_body()
        try:
            created = await rest.create_pod(body)
        except ProvisionError:
            if config.cloud == "COMMUNITY" and config.cloud_fallback:
                body["cloudType"] = "SECURE"
                created = await rest.create_pod(body)
            else:
                raise
        pod_id = created.get("id") or created.get("pod", {}).get("id")
        if not pod_id:
            raise ProvisionError(f"could not parse pod id from create response: {created}")

        p = Pod(rest, pod_id, config)
        try:
            await p._wait_provision()
            await p._wait_ready()
            yield p
        finally:
            if not keep:
                with contextlib.suppress(Exception):
                    await p.teardown()
