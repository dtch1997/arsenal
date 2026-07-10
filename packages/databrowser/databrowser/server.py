"""Serve a built browser over a local HTTP server + a public tunnel.

The server (`python -m http.server`) is launched detached so it outlives the
calling process: a :class:`Viewer` is returned with the public URL and an
explicit :meth:`Viewer.stop`. This mirrors the report-viewer service model —
start it, get a URL, keep browsing.

The public URL comes from the shared `lobby <https://github.com/dtch1997/lobby>`_
hub (a hard dependency): one tunnel + one index page across every
browser/report/dashboard. If the hub can't be reached the local URL is
returned instead (and a note is printed), so the tool still works for local
viewing; ``tunnel=False`` skips the hub entirely.
"""

from __future__ import annotations

import os
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
    hub_name: Union[str, None] = None  # registration name on the lobby hub, if any

    @property
    def alive(self) -> bool:
        return _pid_alive(self.http_pid)

    def stop(self) -> None:
        if self.hub_name:
            try:
                import lobby

                lobby.unregister(self.hub_name)
            except Exception:
                pass  # hub gone — nothing to clean

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
    name: Union[str, None] = None,
    strict: bool = True,
    out_dir: Union[str, Path, None] = None,
    port: Union[int, None] = None,
    tunnel: bool = True,
) -> Viewer:
    """Build a browser for ``data`` and serve it; return a :class:`Viewer`.

    ``filter_fields`` is forwarded to :func:`databrowser.build` — by default
    nothing is filterable. ``name`` labels the browser on the lobby hub index
    (defaults to the title, else ``databrowser-<port>``). Set ``tunnel=False``
    to skip the public URL and serve locally only.
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

    if not tunnel:
        return Viewer(url=local_url, local_url=local_url, out_dir=out, http_pid=http_proc.pid)

    # Register with the shared lobby hub — one tunnel + one index page across
    # every browser/report/dashboard. (Without cloudflared the hub itself
    # degrades to a local-only URL, so this still works for local viewing.)
    import lobby

    try:
        public_url = lobby.serve(
            port, name=name or title or f"databrowser-{port}",
            kind="databrowser", title=title, pid=http_proc.pid, cwd=str(out),
        )
    except Exception as e:
        print(f"note: lobby hub unavailable ({e}); serving locally only.", file=sys.stderr)
        return Viewer(url=local_url, local_url=local_url, out_dir=out, http_pid=http_proc.pid)
    # The hub may have uniquified the slug; recover it from the URL.
    hub_name = public_url.rstrip("/").rsplit("/a/", 1)[-1]
    return Viewer(url=public_url, local_url=local_url, out_dir=out,
                  http_pid=http_proc.pid, hub_name=hub_name)
