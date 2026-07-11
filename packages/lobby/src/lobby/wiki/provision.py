"""RunPod plumbing for the wiki: one cheap persistent CPU pod per wiki name.

Pods are created via the RunPod REST API (``rest.runpod.io/v1``) and then left
running; the ``https://<pod-id>-<port>.proxy.runpod.net`` URL is stable for the
pod's lifetime (stop/start included). The server code is delivered with no
image build and no boot-time network dependency: the docker start command
decodes an embedded base64 copy of ``httpd.py`` and runs it. Content lives on
the container disk only (RunPod CPU pods don't honor ``volumeInGb``) — the
durable copy is the client's local mirror, re-pushed after any pod restart.

Sync module — the async surface lives in client.py.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..state import LobbyError

REST_BASE = "https://rest.runpod.io/v1"
IMAGE = "runpod/base:1.0.2-ubuntu2204"
POD_PREFIX = "lobby-wiki-"
PORT = 8080
# RunPod's proxy 403s the default "Python-urllib/x.y" user-agent; anything
# else passes. Sent on every request to the pod (client.py imports it too).
USER_AGENT = "lobby-wiki"


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key and (Path.home() / ".env").exists():
        for line in (Path.home() / ".env").read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "RUNPOD_API_KEY":
                key = v.strip().strip("'\"")
    if not key:
        raise LobbyError("RUNPOD_API_KEY not set (export it or put it in ~/.env)")
    return key


def _rest(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        REST_BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_api_key()}",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return json.loads(data) if data.strip() else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LobbyError(f"runpod {method} {path} failed ({e.code}): {detail}") from None


def _start_cmd() -> list[str]:
    src = (Path(__file__).parent / "httpd.py").read_bytes()
    b64 = base64.b64encode(src).decode()
    script = (
        "command -v python3 >/dev/null || (apt-get update -qq && apt-get install -y -qq python3); "
        f"echo {b64} | base64 -d > /wiki_server.py && exec python3 /wiki_server.py"
    )
    return ["bash", "-c", script]


def pod_url(pod_id: str) -> str:
    return f"https://{pod_id}-{PORT}.proxy.runpod.net"


def pod_env(pod: dict) -> dict:
    """The pod's env as a dict (REST returns a dict, GraphQL a k/v list)."""
    env = pod.get("env") or {}
    if isinstance(env, list):
        env = {e.get("key"): e.get("value") for e in env if isinstance(e, dict)}
    return env


def get_pod(pod_id: str) -> dict | None:
    try:
        return _rest("GET", f"/pods/{pod_id}")
    except LobbyError as e:
        if "(404)" in str(e):
            return None
        raise


def find_pod(name: str) -> dict | None:
    """The newest non-terminated pod named for this wiki, if any."""
    pods = _rest("GET", "/pods") or []
    if isinstance(pods, dict):
        pods = pods.get("data", [])
    mine = [p for p in pods if p.get("name") == POD_PREFIX + name]
    mine.sort(key=lambda p: p.get("createdAt") or "", reverse=True)
    return mine[0] if mine else None


def create_pod(name: str, title: str, token: str | None = None) -> tuple[str, str]:
    """Create the wiki pod; return (pod_id, write token)."""
    token = token or secrets.token_urlsafe(24)
    body = {
        "name": POD_PREFIX + name,
        "imageName": IMAGE,
        "computeType": "CPU",
        "cloudType": "SECURE",
        "vcpuCount": 1,  # ~$0.03/hr; plenty for a stdlib file server
        # No volume: RunPod zeroes volumeInGb on CPU pods. Persistence is the
        # local mirror — after any pod restart, push() restores the tree.
        "containerDiskInGb": 10,
        "ports": [f"{PORT}/http"],
        "dockerStartCmd": _start_cmd(),
        "env": {
            "WIKI_TOKEN": token,
            "WIKI_DATA": "/data/wiki",
            "WIKI_PORT": str(PORT),
            "WIKI_TITLE": title,
        },
    }
    created = _rest("POST", "/pods", body)
    pod_id = created.get("id") if isinstance(created, dict) else None
    if not pod_id:
        raise LobbyError(f"could not parse pod id from create response: {created}")
    return pod_id, token


def start_pod(pod_id: str) -> None:
    _rest("POST", f"/pods/{pod_id}/start")


def stop_pod(pod_id: str) -> None:
    _rest("POST", f"/pods/{pod_id}/stop")


def delete_pod(pod_id: str) -> None:
    try:
        _rest("DELETE", f"/pods/{pod_id}")
    except LobbyError as e:
        if "(404)" not in str(e):  # already gone = success
            raise


def ping(url: str, timeout: float = 5.0) -> dict | None:
    req = urllib.request.Request(f"{url}/api/ping", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("app") == "lobby-wiki" else None


def wait_ready(url: str, wait: float = 900.0) -> dict:
    """Poll until the wiki answers on its proxy URL.

    The wait is generous because a cold boot pulls the image first. The pod
    record (and the local config) already exist by this point, so even on
    timeout a later ``server(...)`` picks the same pod up once it's up.
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        info = ping(url)
        if info:
            return info
        time.sleep(5)
    raise LobbyError(f"wiki at {url} did not come up within {wait:.0f}s "
                     "(check the pod logs in the RunPod console)")
