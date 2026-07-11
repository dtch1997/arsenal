"""Devbox-side client: provision the pod, push code, pull backups.

RunPod plumbing follows lobby.wiki.provision (one cheap persistent CPU pod,
REST API, stable ``https://<pod-id>-<port>.proxy.runpod.net`` URL). The pod's
docker start command carries only ``bootstrap.py`` (base64-embedded); the real
app — everything under ``habitat/app/`` — is pushed as a tar.gz to
``POST /api/code``, which both the bootstrap and the running server accept.

CPU pods have no durable disk, so the durable copy of the database is the
local mirror under ``~/.habitat/backups/`` (pulled via /api/export, ideally on
a cron); ``restore`` re-pushes it after a pod rebuild.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REST_BASE = "https://rest.runpod.io/v1"
IMAGE = "runpod/base:1.0.2-ubuntu2204"
POD_NAME = "habitat"
PORT = 8080
# RunPod's proxy 403s the default "Python-urllib/x.y" user-agent.
USER_AGENT = "habitat-client"

HOME = Path(os.environ.get("HABITAT_HOME") or Path.home() / ".habitat")
APP_SRC = Path(__file__).parent / "app"
BOOTSTRAP = Path(__file__).parent / "bootstrap.py"


class HabitatError(RuntimeError):
    pass


# -- config -------------------------------------------------------------------

def load_config() -> dict:
    cfg = HOME / "config.json"
    if not cfg.exists():
        raise HabitatError(f"no config at {cfg} — run `habitat provision` first")
    return json.loads(cfg.read_text())


def save_config(config: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = HOME / "config.json"
    cfg.write_text(json.dumps(config, indent=2) + "\n")
    cfg.chmod(0o600)


# -- runpod rest ----------------------------------------------------------------

def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key and (Path.home() / ".env").exists():
        for line in (Path.home() / ".env").read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "RUNPOD_API_KEY":
                key = v.strip().strip("'\"")
    if not key:
        raise HabitatError("RUNPOD_API_KEY not set (export it or put it in ~/.env)")
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
        raise HabitatError(f"runpod {method} {path} failed ({e.code}): {detail}") from None


def pod_url(pod_id: str) -> str:
    return f"https://{pod_id}-{PORT}.proxy.runpod.net"


def find_pod() -> dict | None:
    pods = _rest("GET", "/pods") or []
    if isinstance(pods, dict):
        pods = pods.get("data", [])
    mine = [p for p in pods if p.get("name") == POD_NAME]
    mine.sort(key=lambda p: p.get("createdAt") or "", reverse=True)
    return mine[0] if mine else None


def _start_cmd() -> list[str]:
    b64 = base64.b64encode(BOOTSTRAP.read_bytes()).decode()
    script = (
        "command -v python3 >/dev/null || (apt-get update -qq && apt-get install -y -qq python3); "
        f"echo {b64} | base64 -d > /habitat_bootstrap.py && exec python3 /habitat_bootstrap.py"
    )
    return ["bash", "-c", script]


def create_pod(token: str, tz: str) -> str:
    body = {
        "name": POD_NAME,
        "imageName": IMAGE,
        "computeType": "CPU",
        "cloudType": "SECURE",
        "vcpuCount": 1,  # ~$0.03/hr; plenty for a stdlib sqlite server
        "containerDiskInGb": 10,
        "ports": [f"{PORT}/http"],
        "dockerStartCmd": _start_cmd(),
        "env": {
            "HABITAT_TOKEN": token,
            "HABITAT_TZ": tz,
            "HABITAT_PORT": str(PORT),
            "HABITAT_DATA": "/data",
        },
    }
    created = _rest("POST", "/pods", body)
    pod_id = created.get("id") if isinstance(created, dict) else None
    if not pod_id:
        raise HabitatError(f"could not parse pod id from create response: {created}")
    return pod_id


# -- talking to the pod -----------------------------------------------------------

def _http(method: str, url: str, body: bytes | None = None, token: str | None = None,
          ctype: str = "application/json", timeout: float = 30.0):
    headers = {"User-Agent": USER_AGENT, "Content-Type": ctype}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ping(url: str, timeout: float = 5.0) -> dict | None:
    try:
        info = _http("GET", f"{url}/api/ping", timeout=timeout)
    except Exception:
        return None
    return info if isinstance(info, dict) and str(info.get("app", "")).startswith("habitat") else None


def wait_ready(url: str, wait: float = 900.0, want_app: str | None = None,
               want_version: str | None = None) -> dict:
    deadline = time.time() + wait
    while time.time() < deadline:
        info = ping(url)
        if info and (want_app is None or info.get("app") == want_app) \
                and (want_version is None or info.get("version") == want_version):
            return info
        time.sleep(4)
    raise HabitatError(f"{url} did not answer as expected within {wait:.0f}s "
                       "(check the pod logs in the RunPod console)")


# -- app bundle --------------------------------------------------------------------

def app_tarball() -> tuple[bytes, str]:
    """tar.gz of the app directory + a VERSION file; returns (bytes, version)."""
    files = sorted(p for p in APP_SRC.rglob("*") if p.is_file())
    digest = hashlib.sha256()
    for p in files:
        digest.update(p.name.encode() + b"\0" + p.read_bytes())
    ver = digest.hexdigest()[:10]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in files:
            tar.add(p, arcname=str(p.relative_to(APP_SRC)))
        vinfo = tarfile.TarInfo("VERSION")
        vinfo.size = len(ver)
        tar.addfile(vinfo, io.BytesIO(ver.encode()))
    return buf.getvalue(), ver


def push_code(url: str, token: str) -> str:
    """Push the local app to the pod (bootstrap or live server); return version."""
    tarball, ver = app_tarball()
    info = ping(url)
    if info and info.get("version") == ver:
        return ver  # already current
    try:
        _http("POST", f"{url}/api/code", tarball, token, "application/gzip", timeout=120)
    except urllib.error.HTTPError as e:
        raise HabitatError(f"code push failed ({e.code}): "
                           f"{e.read().decode('utf-8', 'replace')[:300]}") from None
    except Exception:
        pass  # server restarts mid-response; the version check below decides
    wait_ready(url, wait=120, want_app="habitat", want_version=ver)
    return ver


# -- lifecycle ----------------------------------------------------------------------

def provision(tz: str = "Europe/London", token: str | None = None) -> dict:
    """Find-or-create the pod, push the app, save local config."""
    pod = find_pod()
    if pod:
        config = load_config() if (HOME / "config.json").exists() else {}
        token = token or config.get("token")
        if not token:
            raise HabitatError(f"pod {pod['id']} exists but no local config with its "
                               "token — pass token= or delete the pod")
        pod_id = pod["id"]
        if str(pod.get("desiredStatus", "")).upper() != "RUNNING":
            _rest("POST", f"/pods/{pod_id}/start")
    else:
        token = token or secrets.token_urlsafe(16)
        pod_id = create_pod(token, tz)
    url = pod_url(pod_id)
    config = {"pod_id": pod_id, "url": url, "token": token, "tz": tz}
    save_config(config)
    wait_ready(url)  # bootstrap or app, whichever
    ver = push_code(url, token)
    config["version"] = ver
    save_config(config)
    return config


def backup(config: dict | None = None) -> Path:
    """Pull /api/export into ~/.habitat/backups/ (latest.json + timestamped)."""
    config = config or load_config()
    dump = _http("GET", f"{config['url']}/api/export", token=config["token"])
    if dump.get("app") != "habitat":
        raise HabitatError("export did not look like a habitat dump")
    bdir = HOME / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(dump, indent=1)
    (bdir / f"habitat-{stamp}.json").write_text(payload)
    latest = bdir / "latest.json"
    latest.write_text(payload)
    # keep the newest 60 timestamped snapshots
    snaps = sorted(bdir.glob("habitat-*.json"))
    for old in snaps[:-60]:
        old.unlink()
    return latest


def restore(config: dict | None = None, dump_file: Path | None = None) -> dict:
    config = config or load_config()
    src = dump_file or (HOME / "backups" / "latest.json")
    if not Path(src).exists():
        raise HabitatError(f"no dump at {src}")
    dump = json.loads(Path(src).read_text())
    return _http("POST", f"{config['url']}/api/import",
                 json.dumps(dump).encode(), config["token"])


def seed(seed_file: Path, config: dict | None = None) -> list[str]:
    """Create habits from a JSON list; skips names that already exist."""
    config = config or load_config()
    url, token = config["url"], config["token"]
    entries = json.loads(Path(seed_file).read_text())
    existing = {h["name"] for h in
                _http("GET", f"{url}/api/summary", token=token)["habits"]}
    added = []
    for e in entries:
        if e["name"] in existing:
            continue
        _http("POST", f"{url}/api/habits", json.dumps(e).encode(), token)
        added.append(e["name"])
    return added
