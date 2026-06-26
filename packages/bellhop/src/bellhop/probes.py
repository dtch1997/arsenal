"""Readiness probes — the pluggable definition of "functional".

``desiredStatus == RUNNING`` is necessary but not sufficient: sshd / your
server lags the RUNNING state by 30-60s. So once the pod has an IP + mapped
port, the runner runs a probe until it passes. "Functional" is caller-specific,
so it's an injectable callable:

    async def __call__(self, pod) -> bool

Return True when ready, False to keep polling. Raising is also treated as
not-ready (it gets retried until the timeout).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .pod import Pod


@runtime_checkable
class ReadyProbe(Protocol):
    async def __call__(self, pod: "Pod") -> bool: ...


@dataclass
class TcpProbe:
    """Ready when a TCP connect to the mapped ``container_port`` succeeds."""

    container_port: int = 22

    async def __call__(self, pod: "Pod") -> bool:
        host, port = pod.host, pod.mapped_port(self.container_port)
        if not host or not port:
            return False
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False


@dataclass
class SshProbe:
    """Ready when ``ssh root@pod <cmd>`` exits 0 (the gate the bash driver uses)."""

    cmd: str = "true"

    async def __call__(self, pod: "Pod") -> bool:
        try:
            res = await pod._ssh_raw(self.cmd, timeout=15)
            return res.exit_code == 0
        except Exception:
            return False


@dataclass
class HttpProbe:
    """Ready when an HTTP GET to the mapped port returns ``expect_status``.

    Uses the RunPod proxy URL (``https://<id>-<port>.proxy.runpod.net``) by
    default, which works even before a public IP is assigned.
    """

    container_port: int
    path: str = "/"
    expect_status: int = 200
    via_proxy: bool = True

    async def __call__(self, pod: "Pod") -> bool:
        import httpx

        if self.via_proxy:
            url = pod.proxy_url(self.container_port).rstrip("/") + self.path
        else:
            host, port = pod.host, pod.mapped_port(self.container_port)
            if not host or not port:
                return False
            url = f"http://{host}:{port}{self.path}"
        try:
            async with httpx.AsyncClient(timeout=10, verify=True) as c:
                r = await c.get(url)
                return r.status_code == self.expect_status
        except Exception:
            return False


@dataclass
class LogMarkerProbe:
    """Ready when the pod's container logs contain ``marker``.

    For headless pods that run a job in their docker start command (no SSH job
    to probe). Reads logs over SSH (``cat`` of the container log is image
    dependent; by default we shell ``true`` — override ``log_cmd`` per image).
    """

    marker: str
    log_cmd: str = "cat /var/log/*.log 2>/dev/null; journalctl -n 200 2>/dev/null"

    async def __call__(self, pod: "Pod") -> bool:
        try:
            res = await pod._ssh_raw(self.log_cmd, timeout=20)
            return self.marker in res.stdout
        except Exception:
            return False
