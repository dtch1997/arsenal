"""Serve a built browser over a local HTTP server + Cloudflare quick tunnel.

The server (`python -m http.server`) and the tunnel (`cloudflared tunnel
--url ...`) are launched detached so they outlive the calling process: a
:class:`Viewer` is returned with the public URL and an explicit :meth:`Viewer.stop`.
This mirrors the report-viewer service model — start it, get a URL, keep browsing.

If ``cloudflared`` is not on PATH the local URL is returned instead (and a note
is printed), so the tool still works for local viewing.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, Union

from .core import FilterSpec, build

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_URL_WAIT_SECS = 40


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 5.0) -> bool:
    """Block until 127.0.0.1:port accepts a connection, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _pid_alive(pid: Union[int, None]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A SIGTERM'd child we haven't reaped lingers as a zombie; os.kill(0) still
    # succeeds on it. Treat zombies as dead (Linux /proc state field).
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except OSError:
        pass
    return True


@dataclass
class Viewer:
    """Handle to a running browser. Call :meth:`stop` when done."""

    url: str
    local_url: str
    out_dir: Path
    http_pid: Union[int, None] = None
    tunnel_pid: Union[int, None] = None

    @property
    def alive(self) -> bool:
        return _pid_alive(self.http_pid)

    def stop(self) -> None:
        for pid in (self.tunnel_pid, self.http_pid):
            if not _pid_alive(pid):
                continue
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            # Reap if it's our child, so it doesn't linger as a zombie.
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass


def serve(
    data: Union[str, Path, Iterable[dict]],
    *,
    filter_fields: Union[Sequence[FilterSpec], None] = None,
    title: Union[str, None] = None,
    strict: bool = True,
    out_dir: Union[str, Path, None] = None,
    port: Union[int, None] = None,
    tunnel: bool = True,
) -> Viewer:
    """Build a browser for ``data`` and serve it; return a :class:`Viewer`.

    ``filter_fields`` is forwarded to :func:`databrowser.build` — by default
    nothing is filterable. Set ``tunnel=False`` to skip Cloudflare and serve
    locally only.
    """
    if out_dir is None:
        out_dir = Path(tempfile.mkdtemp(prefix="databrowser-"))
    out = build(data, out_dir, filter_fields=filter_fields, title=title, strict=strict)

    port = port or _free_port()
    http_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(out),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    local_url = f"http://127.0.0.1:{port}"
    if not _wait_port(port):
        http_proc.terminate()
        raise RuntimeError(f"local HTTP server failed to start on port {port}")

    if not tunnel or not shutil.which("cloudflared"):
        if tunnel:
            print("note: cloudflared not found on PATH; serving locally only.", file=sys.stderr)
        return Viewer(url=local_url, local_url=local_url, out_dir=out, http_pid=http_proc.pid)

    log_path = out / "cloudflared.log"
    with log_path.open("w") as log:
        tunnel_proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", local_url, "--no-autoupdate"],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.time() + _URL_WAIT_SECS
    public_url = None
    while time.time() < deadline:
        if log_path.exists():
            m = _URL_RE.search(log_path.read_text(errors="ignore"))
            if m:
                public_url = m.group(0)
                break
        time.sleep(0.4)

    if not public_url:
        print("note: tunnel URL did not appear in time; returning local URL.", file=sys.stderr)
        public_url = local_url

    return Viewer(
        url=public_url,
        local_url=local_url,
        out_dir=out,
        http_pid=http_proc.pid,
        tunnel_pid=tunnel_proc.pid,
    )
