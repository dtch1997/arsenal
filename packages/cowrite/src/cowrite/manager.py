"""Lifecycle: start an editor (server + optional tunnel), list, stop, prune.

The HTTP server is launched **detached** (`start_new_session=True`) so it
OUTLIVES the launching process — the human keeps editing after the CLI returns.
The public URL comes from the shared lobby hub (github.com/dtch1997/lobby):
one tunnel + one index page across every editor/dashboard. State for each editor is recorded
per-slug under a user state dir so `list`/`stop` work across sessions, and
teardown is explicit so nothing is orphaned silently.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import lobby

MD_EXTS = (".md", ".markdown")


# --------------------------------------------------------------------------- #
# state location (user dir, so it works from an installed package)
# --------------------------------------------------------------------------- #
def state_dir() -> Path:
    base = os.environ.get("COWRITE_STATE_DIR")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "cowrite"
    return Path.home() / ".cowrite" / "state"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return s or "draft"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _state_path(slug: str) -> Path:
    return state_dir() / f"{slug}.json"


def _load_state(slug: str) -> dict | None:
    p = _state_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _all_states() -> list[dict]:
    sd = state_dir()
    if not sd.exists():
        return []
    out = []
    for p in sorted(sd.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_alive(st: dict) -> bool:
    server_ok = _pid_alive(st.get("server_pid"))
    if st.get("tunnel_pid") is None:  # --no-tunnel editor
        return server_ok
    return server_ok and _pid_alive(st.get("tunnel_pid"))


# --------------------------------------------------------------------------- #
# serve
# --------------------------------------------------------------------------- #
def serve(path: str, slug: str | None = None, title: str | None = None,
          port: int | None = None, no_tunnel: bool = False) -> dict:
    draft = Path(path).expanduser().resolve()
    if draft.is_dir():
        _die(f"path is a directory; point at a single Markdown file: {draft}")
    if draft.suffix.lower() not in MD_EXTS:
        _die(f"cowrite edits Markdown; got '{draft.suffix}'. Use a .md/.markdown file.")
    if not draft.exists():
        # A draft you intend to create is fine — start it empty.
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("", encoding="utf-8")

    slug = _slugify(slug) if slug else _slugify(draft.stem)
    sd = state_dir()
    sd.mkdir(parents=True, exist_ok=True)

    existing = _load_state(slug)
    if existing and _is_alive(existing):
        _die(
            f"an editor named '{slug}' is already live at {existing.get('full_url')}.\n"
            f"  reuse it, or `cowrite stop {slug}` first, or pass a different --slug."
        )

    port = port or _free_port()
    title = title or draft.stem
    srv_log_path = sd / f"{slug}.http.log"

    # 1) The editor HTTP server, detached so it outlives this process.
    srv_log = open(srv_log_path, "wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "cowrite", "_server",
         "--file", str(draft), "--port", str(port), "--title", title],
        stdout=srv_log, stderr=subprocess.STDOUT, start_new_session=True,
    )

    url = None
    if no_tunnel:
        # Local-only: wait until the server accepts connections.
        deadline = time.time() + 10
        while time.time() < deadline:
            if server.poll() is not None:
                break
            if _port_open(port):
                url = f"http://127.0.0.1:{port}"
                break
            time.sleep(0.25)
        if not url:
            _kill(server)
            _die(f"server did not come up on port {port}.\n{_tail(srv_log_path)}")
        full_url = url + "/"
    else:
        # Wait until the editor accepts connections (the hub health-checks the port,
        # and there's no point tunnelling a server that failed to boot).
        deadline = time.time() + 10
        while time.time() < deadline:
            if server.poll() is not None or _port_open(port):
                break
            time.sleep(0.25)
        if server.poll() is not None or not _port_open(port):
            _kill(server)
            _die(f"server did not come up on port {port}.\n{_tail(srv_log_path)}")

        # 2) Register with the shared lobby hub — one tunnel + one index page
        # across every editor/dashboard (github.com/dtch1997/lobby).
        try:
            full_url = lobby.serve(port, name=slug, kind="cowrite", title=title,
                                   pid=server.pid, cwd=str(draft.parent))
            url = full_url.rstrip("/")
        except Exception as e:
            _kill(server)
            _die(f"lobby hub unavailable: {e}\n"
                 f"  (see `lobby status`, or use --no-tunnel for local editing)")

    st = {
        "slug": slug,
        "url": url,
        "full_url": full_url,
        "port": port,
        "server_pid": server.pid,
        "tunnel_pid": None,  # tunnelling is owned by the lobby hub daemon
        "source": str(draft),
        "title": title,
        "started_at": _now(),
    }
    _state_path(slug).write_text(json.dumps(st, indent=2))
    return st


def _tail(p: Path, n: int = 600) -> str:
    try:
        return p.read_text(errors="replace")[-n:]
    except Exception:
        return ""


def _kill(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# list / stop / prune
# --------------------------------------------------------------------------- #
def list_editors() -> list[dict]:
    return _all_states()


def _teardown(st: dict) -> None:
    for key in ("tunnel_pid", "server_pid"):
        pid = st.get(key)
        if not _pid_alive(pid):
            continue
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    slug = st.get("slug", "")
    if slug:
        sd = state_dir()
        for p in (_state_path(slug), sd / f"{slug}.cloudflared.log", sd / f"{slug}.http.log"):
            p.unlink(missing_ok=True)
        try:
            lobby.unregister(slug)
        except Exception:
            pass  # hub gone or never registered — nothing to clean


def prune() -> list[str]:
    pruned = []
    for st in _all_states():
        if not _is_alive(st):
            _teardown(st)
            pruned.append(st.get("slug", "?"))
    return pruned


def stop(slug: str | None = None, all_: bool = False) -> list[str]:
    if all_:
        stopped = []
        for st in _all_states():
            _teardown(st)
            stopped.append(st.get("slug", "?"))
        return stopped
    if not slug:
        _die("stop needs a <slug> or --all")
    st = _load_state(slug)
    if not st:
        _die(f"no editor named '{slug}' (try `cowrite list`)")
    _teardown(st)
    return [slug]
