"""Drop-in client API: register a running local app with the hub, get a public URL."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request

from . import state
from .state import LobbyError


def _hub_port(port: int | None = None) -> int:
    return port or int(os.environ.get("LOBBY_PORT") or state.DEFAULT_PORT)


def _ping(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("app") == "lobby" else None


def _post(port: int, path: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ensure_hub(*, hub_port: int | None = None, tunnel: bool = True, wait: float = 75.0) -> dict:
    """Make sure the hub daemon is up (starting it detached if needed); return its ping info."""
    port = _hub_port(hub_port)
    if state.port_open(port) and _ping(port) is None:
        raise LobbyError(
            f"port {port} is serving something that is not a lobby hub; "
            "set LOBBY_PORT to use a different port"
        )
    # flock so concurrent first-callers spawn exactly one daemon
    with open(state.state_dir() / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if _ping(port) is None:
                log = open(state.state_dir() / "hub.log", "ab")
                argv = [sys.executable, "-m", "lobby.cli", "_daemon", "--port", str(port)]
                if not tunnel:
                    argv.append("--no-tunnel")
                subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    deadline = time.time() + wait  # tunnel acquisition alone can take ~40s
    while time.time() < deadline:
        info = _ping(port)
        if info and info.get("ready"):
            return info
        time.sleep(0.3)
    raise LobbyError(
        f"lobby hub on port {port} did not become ready within {wait:.0f}s "
        f"(see {state.state_dir() / 'hub.log'})"
    )


def serve(
    port: int,
    *,
    name: str | None = None,
    kind: str = "app",
    title: str | None = None,
    pid: int | None = None,
    cwd: str | None = None,
    entry: str = "",
    hub_port: int | None = None,
    tunnel: bool = True,
) -> str:
    """Register an already-listening 127.0.0.1:<port> app; return its public URL.

    `pid` should be the serving process (defaults to the caller); the hub uses it
    plus a TCP probe for liveness. `entry` is appended to the returned URL.
    """
    ensure_hub(hub_port=hub_port, tunnel=tunnel)
    resp = _post(
        _hub_port(hub_port),
        "/api/register",
        {
            "name": name or f"{kind}-{port}",
            "port": port,
            "kind": kind,
            "title": title,
            "pid": os.getpid() if pid is None else pid,
            "cwd": cwd or os.getcwd(),
            "started_at": time.time(),
        },
    )
    return resp["url"].rstrip("/") + "/" + entry.lstrip("/") if entry else resp["url"]


def serve_dir(
    directory: str,
    *,
    name: str | None = None,
    kind: str = "static",
    title: str | None = None,
    entry: str = "",
    port: int | None = None,
    hub_port: int | None = None,
    tunnel: bool = True,
):
    """Serve a directory of static files through the hub.

    Spawns a detached `python -m http.server` (free port unless `port` is given),
    registers it, and returns `(url, stop)` where `stop()` kills the file server.
    """
    port = port or state.free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(directory)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 10
    while not state.port_open(port):
        if time.time() > deadline or proc.poll() is not None:
            proc.terminate()
            raise LobbyError(f"http.server for {directory!r} failed to start")
        time.sleep(0.1)
    url = serve(
        port, name=name, kind=kind, title=title, pid=proc.pid, entry=entry,
        hub_port=hub_port, tunnel=tunnel,
    )

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return url, stop


def unregister(name: str, *, hub_port: int | None = None) -> None:
    port = _hub_port(hub_port)
    if _ping(port):
        _post(port, "/api/unregister", {"name": name})
    else:
        state.app_path(state.slugify(name)).unlink(missing_ok=True)


def hub_url(*, hub_port: int | None = None) -> str | None:
    """Public URL of the running hub, or None if no hub is up."""
    info = _ping(_hub_port(hub_port))
    return info.get("url") if info else None
