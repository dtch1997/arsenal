"""Devbox-side plumbing for foyer's stable URL (see relay_httpd.py).

Self-healing model: the pod persists its target on disk and never lets its
accept loop die; the devbox (`foyer serve`) runs a keeper that pings every
minute and re-publishes whenever the relay answers with a missing or stale
target fingerprint. `redeploy()` swaps the embedded server code on the
EXISTING pod (PATCH + restart) so the stable URL never changes.

One cheap persistent RunPod CPU pod (pattern cribbed from ``lobby.wiki``):
created via the REST API, server code delivered as an embedded base64 of
``relay_httpd.py`` in the docker start command — no image build, no boot-time
network dependency. The pod's ``https://<pod-id>-8080.proxy.runpod.net`` URL
is stable for its lifetime (stop/start included); ``foyer serve`` publishes
its current quick-tunnel URL to the relay on every start.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from .server import FOYER_HOME

REST_BASE = "https://rest.runpod.io/v1"
IMAGE = "runpod/base:1.0.2-ubuntu2204"
POD_NAME = "foyer-relay"
PORT = 8080
# RunPod's proxy 403s the default "Python-urllib/x.y" user-agent.
USER_AGENT = "foyer-relay"
CONFIG = FOYER_HOME / "relay.json"


class RelayError(RuntimeError):
    pass


# --- runpod REST ------------------------------------------------------------ #

def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key and (Path.home() / ".env").exists():
        for line in (Path.home() / ".env").read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "RUNPOD_API_KEY":
                key = v.strip().strip("'\"")
    if not key:
        raise RelayError("RUNPOD_API_KEY not set (export it or put it in ~/.env)")
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
        raise RelayError(f"runpod {method} {path} failed ({e.code}): {detail}") from None


def _start_cmd() -> list[str]:
    src = (Path(__file__).parent / "relay_httpd.py").read_bytes()
    b64 = base64.b64encode(src).decode()
    script = (
        "command -v python3 >/dev/null || (apt-get update -qq && apt-get install -y -qq python3); "
        f"echo {b64} | base64 -d > /relay.py && exec python3 /relay.py"
    )
    return ["bash", "-c", script]


def stable_url(pod_id: str) -> str:
    return f"https://{pod_id}-{PORT}.proxy.runpod.net"


def find_pod() -> dict | None:
    pods = _rest("GET", "/pods") or []
    if isinstance(pods, dict):
        pods = pods.get("data", [])
    mine = [p for p in pods if p.get("name") == POD_NAME]
    mine.sort(key=lambda p: p.get("createdAt") or "", reverse=True)
    return mine[0] if mine else None


def create_pod(token: str) -> str:
    body = {
        "name": POD_NAME,
        "imageName": IMAGE,
        "computeType": "CPU",
        "cloudType": "SECURE",
        "vcpuCount": 1,  # ~$0.03/hr; a byte pump needs nothing more
        "containerDiskInGb": 10,
        "ports": [f"{PORT}/http"],
        "dockerStartCmd": _start_cmd(),
        "env": {"RELAY_TOKEN": token, "RELAY_PORT": str(PORT)},
    }
    created = _rest("POST", "/pods", body)
    pod_id = created.get("id") if isinstance(created, dict) else None
    if not pod_id:
        raise RelayError(f"could not parse pod id from create response: {created}")
    return pod_id


def target_fp(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:8] if url else ""


def redeploy(wait: float = 900.0) -> str:
    """Swap the embedded relay server on the existing pod — same id, same URL."""
    cfg = config()
    if not cfg:
        raise RelayError("no relay configured — run `foyer relay up` first")
    pod_id = cfg["pod_id"]
    _rest("PATCH", f"/pods/{pod_id}", {"dockerStartCmd": _start_cmd()})
    try:
        _rest("POST", f"/pods/{pod_id}/restart")
    except RelayError:  # restart endpoint is flaky; stop/start does the same
        _rest("POST", f"/pods/{pod_id}/stop")
        time.sleep(10)
        _rest("POST", f"/pods/{pod_id}/start")
    base = stable_url(pod_id)
    deadline = time.time() + wait
    while time.time() < deadline:
        if ping(base):
            return base
        time.sleep(5)
    raise RelayError(f"relay at {base} did not come back within {wait:.0f}s")


def delete_pod(pod_id: str) -> None:
    try:
        _rest("DELETE", f"/pods/{pod_id}")
    except RelayError as e:
        if "(404)" not in str(e):
            raise


# --- relay control ---------------------------------------------------------- #

def _relay_req(base: str, path: str, body: dict | None = None,
               token: str | None = None, timeout: float = 10.0):
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ping(base: str, timeout: float = 5.0) -> dict | None:
    try:
        info = _relay_req(base, "/_foyer/ping", timeout=timeout)
    except Exception:
        return None
    return info if isinstance(info, dict) and info.get("app") == "foyer-relay" else None


def config() -> dict | None:
    if not CONFIG.exists():
        return None
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def publish(target_url: str, attempts: int = 6) -> str:
    """Point the relay at `target_url`; returns the stable URL."""
    cfg = config()
    if not cfg:
        raise RelayError("no relay configured — run `foyer relay up` first")
    base = stable_url(cfg["pod_id"])
    last: Exception | None = None
    for _ in range(attempts):
        try:
            _relay_req(base, "/_foyer/target", {"url": target_url},
                       token=cfg["token"])
            return base
        except Exception as e:  # proxy hiccup / pod waking up
            last = e
            time.sleep(5)
    raise RelayError(f"could not publish target to relay at {base}: {last!r}")


def up(wait: float = 900.0) -> str:
    """Find-or-create the relay pod, wait until it answers, save config."""
    cfg = config() or {}
    token = cfg.get("token") or secrets.token_urlsafe(24)
    pod = find_pod()
    if pod is None:
        pod_id = create_pod(token)
    else:
        pod_id = pod["id"]
        if cfg.get("pod_id") == pod_id and cfg.get("token"):
            token = cfg["token"]
        elif not cfg.get("token"):
            # Pod exists but we lost the local config; its env holds the token.
            env = pod.get("env") or {}
            if isinstance(env, list):
                env = {e.get("key"): e.get("value") for e in env
                       if isinstance(e, dict)}
            token = env.get("RELAY_TOKEN") or token
    FOYER_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"pod_id": pod_id, "token": token}, indent=2))
    base = stable_url(pod_id)
    deadline = time.time() + wait
    while time.time() < deadline:
        if ping(base):
            return base
        time.sleep(5)
    raise RelayError(f"relay at {base} did not come up within {wait:.0f}s "
                     "(check the pod logs in the RunPod console)")
