"""Async client API for the wiki: find-or-create a named server, pull/push state.

The content model is a plain directory tree, with a local mirror
(``~/.lobby/wiki/<name>/``) as the workspace and durable copy: change
arbitrary files there and ``push()`` uploads the tree atomically;
``add()``/``rm()`` are conveniences that mutate the mirror and push.
``pull()`` is the explicit sync-down (adopting a wiki written from another
machine). Because the mirror is authoritative, the pod is disposable —
``server(...)`` + ``push()`` rebuild it from scratch.

All functions are async; the underlying HTTP is stdlib urllib run in a worker
thread (lobby has no dependencies).
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import state
from ..state import LobbyError
from . import provision
from .httpd import _safe_extract

DEFAULT_NAME = "wiki"


def _wiki_root() -> Path:
    d = state.state_dir() / "wiki"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path(name: str) -> Path:
    return _wiki_root() / f"{name}.json"


def mirror_dir(name: str) -> Path:
    d = _wiki_root() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


class Wiki:
    """A live wiki server. Construct via :func:`server`."""

    def __init__(self, name: str, url: str, token: str, pod_id: str):
        self.name = name
        self.url = url.rstrip("/")
        self.token = token
        self.pod_id = pod_id

    def __repr__(self) -> str:
        return f"Wiki({self.name!r}, url={self.url!r})"

    # -- state transfer ----------------------------------------------------

    def _request(self, method: str, path: str, *, body: bytes | None = None,
                 timeout: float = 120.0) -> bytes:
        req = urllib.request.Request(
            self.url + path, data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "User-Agent": provision.USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise LobbyError(f"wiki server said {e.code} for {path}: {detail}") from None
        except OSError as e:
            raise LobbyError(f"cannot reach wiki at {self.url}: {e}") from None

    async def pull(self, dest: str | Path | None = None) -> Path:
        """Download the server's whole tree into ``dest`` (default: the mirror).

        ``dest`` is replaced, not merged — after pull it equals server state.
        """
        dest = Path(dest).expanduser() if dest else mirror_dir(self.name)
        raw = await asyncio.to_thread(self._request, "GET", "/api/state")
        tmp = dest.parent / f".{dest.name}-pull"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                _safe_extract(tar, tmp)
        except (tarfile.TarError, ValueError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise LobbyError(f"bad state archive from server: {e}") from None
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
        return dest

    async def push(self, src: str | Path | None = None) -> int:
        """Upload ``src`` (default: the mirror) as the server's new tree.

        The replacement is atomic and total — the server tree becomes exactly
        ``src``. Returns the number of files now on the server.
        """
        src = Path(src).expanduser() if src else mirror_dir(self.name)
        if not src.is_dir():
            raise LobbyError(f"push source is not a directory: {src}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for p in sorted(src.rglob("*")):
                rel = p.relative_to(src)
                if not any(part.startswith(".") for part in rel.parts):
                    tar.add(p, arcname=str(rel), recursive=False)
        resp = await asyncio.to_thread(
            self._request, "POST", "/api/state", body=buf.getvalue())
        return json.loads(resp)["files"]

    # -- conveniences on top of the mirror ------------------------------------
    #
    # These treat the mirror as the local workspace and source of truth: they
    # mutate it and push the whole thing. They never pull implicitly — a
    # freshly recreated (empty) pod must not be able to wipe the durable copy.

    async def add(self, path: str | Path, *, name: str | None = None) -> str:
        """Copy one file or directory into the mirror, push, return its URL."""
        src = Path(path).expanduser()
        if not src.exists():
            raise LobbyError(f"no such file or directory: {src}")
        tree = mirror_dir(self.name)
        if src.is_dir():
            rel = state.slugify(name or src.resolve().name)
            shutil.rmtree(tree / rel, ignore_errors=True)
            shutil.copytree(src, tree / rel)
            rel += "/"
        else:
            stem = state.slugify(name or src.stem)
            rel = stem + (".md" if src.suffix.lower() == ".markdown"
                          else src.suffix.lower())
            shutil.copy2(src, tree / rel)
        await self.push()
        return f"{self.url}/{rel}"

    async def rm(self, rel_path: str) -> None:
        """Remove a file or directory from the mirror (and push)."""
        tree = mirror_dir(self.name)
        target = (tree / rel_path.strip("/")).resolve()
        if tree.resolve() not in target.parents:
            raise LobbyError(f"path escapes the tree: {rel_path!r}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        await self.push()

    async def ls(self) -> list[str]:
        """Top-level entries of the mirror (dirs get a trailing slash).

        The mirror equals server state after any add/rm/push/pull; if the
        server was written from elsewhere, ``pull()`` first.
        """
        tree = mirror_dir(self.name)
        return sorted(p.name + ("/" if p.is_dir() else "")
                      for p in tree.iterdir() if not p.name.startswith("."))

    # -- lifecycle -----------------------------------------------------------

    async def ping(self) -> dict | None:
        return await asyncio.to_thread(provision.ping, self.url)

    async def status(self) -> dict:
        pod = await asyncio.to_thread(provision.get_pod, self.pod_id)
        info = await self.ping() if pod else None
        return {
            "name": self.name,
            "url": self.url,
            "pod_id": self.pod_id,
            "pod_status": (pod or {}).get("desiredStatus", "TERMINATED"),
            "serving": bool(info),
            "files": (info or {}).get("files"),
            "cost_per_hr": (pod or {}).get("costPerHr"),
        }

    async def stop(self) -> None:
        """Stop the pod (content and URL survive; ``server(...)`` restarts it)."""
        await asyncio.to_thread(provision.stop_pod, self.pod_id)

    async def destroy(self) -> None:
        """Terminate the pod and forget it (the local mirror is kept)."""
        await asyncio.to_thread(provision.delete_pod, self.pod_id)
        config_path(self.name).unlink(missing_ok=True)


async def server(name: str = DEFAULT_NAME, *, title: str | None = None,
                 recreate: bool = False) -> Wiki:
    """Find or create the wiki server called ``name``; return a live handle.

    Resolution order: the locally recorded pod, then any RunPod pod named
    ``lobby-wiki-<name>`` (so a second machine adopts an existing wiki), then
    a fresh pod. A stopped pod is started; ``recreate=True`` terminates and
    re-creates it (new URL, fresh server code — push the mirror after).
    """
    return await asyncio.to_thread(_server_sync, name, title, recreate)


def _server_sync(name: str, title: str | None, recreate: bool) -> Wiki:
    name = state.slugify(name)
    cfg = state.read_json(config_path(name)) or {}
    pod = provision.get_pod(cfg["pod_id"]) if cfg.get("pod_id") else None
    if pod is None:
        pod = provision.find_pod(name)
    if pod and recreate:
        provision.delete_pod(pod["id"])
        pod = None

    if pod:
        token = cfg.get("token") or provision.pod_env(pod).get("WIKI_TOKEN")
        if not token:
            raise LobbyError(
                f"found pod {pod['id']} for wiki {name!r} but no write token "
                "(no local config and the pod env is not readable); "
                "recreate=True re-provisions it"
            )
        if (pod.get("desiredStatus") or "").upper() == "EXITED":
            provision.start_pod(pod["id"])
        pod_id = pod["id"]
    else:
        pod_id, token = provision.create_pod(name, title or name,
                                             token=cfg.get("token"))

    url = provision.pod_url(pod_id)
    state.write_json(config_path(name), {
        "name": name, "pod_id": pod_id, "url": url, "token": token,
        "updated_at": time.time(),
    })
    provision.wait_ready(url)
    return Wiki(name, url, token, pod_id)
