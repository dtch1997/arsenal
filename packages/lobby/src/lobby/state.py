"""File-backed state for the lobby hub: one JSON record per app, one for the hub."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from pathlib import Path

DEFAULT_PORT = 4777


class LobbyError(RuntimeError):
    pass


def state_dir() -> Path:
    env = os.environ.get("LOBBY_STATE_DIR")
    if env:
        d = Path(env)
    elif os.environ.get("XDG_STATE_HOME"):
        d = Path(os.environ["XDG_STATE_HOME"]) / "lobby"
    else:
        d = Path.home() / ".lobby"
    (d / "apps").mkdir(parents=True, exist_ok=True)
    return d


def hub_path() -> Path:
    return state_dir() / "hub.json"


def app_path(name: str) -> Path:
    return state_dir() / "apps" / f"{name}.json"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "app"


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def list_apps() -> list[dict]:
    apps = []
    for f in sorted((state_dir() / "apps").glob("*.json")):
        data = read_json(f)
        if data:
            apps.append(data)
    return apps


def app_live(app: dict) -> bool:
    """An app is live if its port accepts connections (and its pid, when recorded, exists)."""
    pid = app.get("pid")
    if pid and not pid_alive(pid):
        return False
    return port_open(app["port"])


def ago(ts: float | None) -> str:
    if not ts:
        return "?"
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m ago"
    return f"{s // 86400}d ago"
