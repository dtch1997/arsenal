"""The foyer web server.

Everything is behind a single random token (``~/.foyer/token``) because a
terminal onto your tmux server is shell access: the first visit carries
``?t=<token>``, which is exchanged for a cookie; every route — pages, APIs,
the websocket — refuses without one or the other.

The terminal itself is a PTY bridge: each websocket connection spawns a
``tmux attach`` client on a fresh PTY and pumps bytes both ways. Output goes
to the browser as binary frames; input and resizes arrive as JSON text
frames. Closing the socket SIGTERMs the tmux client, which detaches cleanly —
the session itself is never touched.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import secrets
import signal
import struct
import termios
from pathlib import Path

from aiohttp import WSMsgType, web

from . import sessions

FOYER_HOME = Path(os.environ.get("FOYER_HOME", str(Path.home() / ".foyer")))
STATIC = Path(__file__).parent / "static"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
COOKIE = "foyer_token"
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def load_token() -> str:
    FOYER_HOME.mkdir(parents=True, exist_ok=True)
    tf = FOYER_HOME / "token"
    if not tf.exists():
        tf.write_text(secrets.token_urlsafe(24) + "\n")
        tf.chmod(0o600)
    return tf.read_text().strip()


# --- auth ------------------------------------------------------------------ #

@web.middleware
async def auth(request: web.Request, handler):
    token = request.app["token"]
    supplied = request.query.get("t")
    if supplied is not None and secrets.compare_digest(supplied, token):
        if request.path == "/":  # strip the token from the address bar
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, token, httponly=True, max_age=30 * 86400)
            return resp
        return await handler(request)
    if secrets.compare_digest(request.cookies.get(COOKIE, ""), token):
        return await handler(request)
    raise web.HTTPUnauthorized(
        text="foyer: open the tokened URL printed by `foyer serve` (…/?t=<token>)"
    )


# --- tiny json state files under ~/.foyer ----------------------------------- #

def _read_state(name: str) -> dict:
    path = FOYER_HOME / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(name: str, obj: dict) -> None:
    FOYER_HOME.mkdir(parents=True, exist_ok=True)
    (FOYER_HOME / name).write_text(json.dumps(obj, indent=2))


# --- pages & session API ---------------------------------------------------- #

async def page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def api_sessions(request: web.Request) -> web.Response:
    rows = await asyncio.to_thread(sessions.list_sessions)
    # Manual order first (drag-and-drop in the sidebar), then the rest by
    # recency — so new sessions surface without disturbing the arrangement.
    order = _read_state("order.json").get("names", [])
    rank = {n: i for i, n in enumerate(order)}
    rows.sort(key=lambda r: (rank.get(r["name"], len(order)), -r["activity"]))
    return web.json_response({"sessions": rows})


async def api_order_put(request: web.Request) -> web.Response:
    body = await request.json()
    names = body.get("names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise web.HTTPBadRequest(text="order needs {names: [str, ...]}")
    _write_state("order.json", {"names": names})
    return web.json_response({"ok": True})


# --- thread lifecycle -------------------------------------------------------- #

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
DEFAULT_CONFIG = {"workspace": str(Path.home()), "command": "claude"}


def get_config() -> dict:
    return {**DEFAULT_CONFIG, **_read_state("config.json")}


def _check_dir(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_dir() or not path.is_relative_to(Path.home().resolve()):
        raise web.HTTPBadRequest(text="dir must be a directory under $HOME")
    return path


def _auto_name(base: str, taken: set[str]) -> str:
    n = 1
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


async def api_config(request: web.Request) -> web.Response:
    return web.json_response(get_config())


async def api_thread_create(request: web.Request) -> web.Response:
    body = await request.json()
    cfg = get_config()
    cwd = _check_dir(str(body.get("dir") or cfg["workspace"]))
    taken = await asyncio.to_thread(sessions.names)
    name = str(body.get("name") or "").strip()
    if not name:
        name = _auto_name(cwd.name or "thread", taken)
    if not _NAME_RE.match(name):
        raise web.HTTPBadRequest(
            text="thread names are letters, digits, - and _ only")
    if name in taken:
        raise web.HTTPBadRequest(text=f"a thread named {name!r} already exists")
    try:
        await asyncio.to_thread(sessions.create, name, str(cwd), cfg["command"])
    except RuntimeError as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response({"name": name})


async def api_thread_rename(request: web.Request) -> web.Response:
    old = request.match_info["session"]
    body = await request.json()
    new = str(body.get("name", "")).strip()
    if not _NAME_RE.match(new):
        raise web.HTTPBadRequest(
            text="thread names are letters, digits, - and _ only")
    taken = await asyncio.to_thread(sessions.names)
    if old not in taken:
        raise web.HTTPNotFound(text=f"no thread named {old!r}")
    if new in taken:
        raise web.HTTPBadRequest(text=f"a thread named {new!r} already exists")
    try:
        await asyncio.to_thread(sessions.rename, old, new)
    except RuntimeError as e:
        raise web.HTTPBadRequest(text=str(e))
    # Per-thread state is keyed by name — carry it across the rename.
    old_notes = _notes_path(old)
    if old_notes.exists():
        old_notes.rename(_notes_path(new))
    roots = _read_state("plotroots.json")
    if old in roots:
        roots[new] = roots.pop(old)
        _write_state("plotroots.json", roots)
    order = _read_state("order.json")
    if old in order.get("names", []):
        order["names"] = [new if n == old else n for n in order["names"]]
        _write_state("order.json", order)
    return web.json_response({"name": new})


async def api_thread_delete(request: web.Request) -> web.Response:
    name = request.match_info["session"]
    taken = await asyncio.to_thread(sessions.names)
    if name not in taken:
        raise web.HTTPNotFound(text=f"no thread named {name!r}")
    try:
        await asyncio.to_thread(sessions.kill, name)
    except RuntimeError as e:
        raise web.HTTPBadRequest(text=str(e))
    # Drop state that is meaningless without the session; keep the notes
    # file — it may be the only record of what the thread was for.
    roots = _read_state("plotroots.json")
    if name in roots:
        roots.pop(name)
        _write_state("plotroots.json", roots)
    order = _read_state("order.json")
    if name in order.get("names", []):
        order["names"] = [n for n in order["names"] if n != name]
        _write_state("order.json", order)
    return web.json_response({"ok": True})


# --- terminal bridge --------------------------------------------------------- #

async def ws_terminal(request: web.Request) -> web.WebSocketResponse:
    target = request.query.get("target", "")
    if not target or not await asyncio.to_thread(sessions.exists, target):
        raise web.HTTPNotFound(text=f"no tmux session named {target!r}")

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    master, slave = os.openpty()
    # A sane size before the client's first resize message lands; without it
    # the tmux client starts at 0x0 and renders nothing.
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    proc = await asyncio.create_subprocess_exec(
        *sessions.tmux_cmd(), "attach-session", "-t", f"={target}",
        stdin=slave, stdout=slave, stderr=slave,
        env={**os.environ, "TERM": "xterm-256color"},
        start_new_session=True,
    )
    os.close(slave)

    def set_winsize(rows: int, cols: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.ioctl(master, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        # The PTY is not the child's controlling terminal, so the kernel won't
        # deliver SIGWINCH for us — poke the tmux client's process group directly.
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(proc.pid, signal.SIGWINCH)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)

    def on_output() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if not data:  # EOF: the tmux client exited/detached
            loop.remove_reader(master)
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(data)

    loop.add_reader(master, on_output)

    async def pump() -> None:
        while True:
            data = await queue.get()
            if not data:
                break
            await ws.send_bytes(data)
        await ws.close()

    pump_task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if m.get("type") == "input":
                os.write(master, str(m.get("data", "")).encode())
            elif m.get("type") == "resize":
                set_winsize(int(m.get("rows", 24)), int(m.get("cols", 80)))
    finally:
        pump_task.cancel()
        with contextlib.suppress(OSError):
            loop.remove_reader(master)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()  # tmux client detaches on SIGTERM
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 5)
        with contextlib.suppress(OSError):
            os.close(master)
    return ws


# --- notes ------------------------------------------------------------------- #

def _notes_path(session: str) -> Path:
    slug = _SLUG_RE.sub("-", session).strip("-") or "unnamed"
    notes_dir = FOYER_HOME / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir / f"{slug}.md"


async def api_notes_get(request: web.Request) -> web.Response:
    path = _notes_path(request.match_info["session"])
    text = path.read_text() if path.exists() else ""
    return web.json_response({"text": text})


async def api_notes_put(request: web.Request) -> web.Response:
    body = await request.json()
    path = _notes_path(request.match_info["session"])
    path.write_text(str(body.get("text", "")))
    return web.json_response({"ok": True})


# --- plots (recent images near the session's cwd) ---------------------------- #

def _find_images(root: Path, depth: int = 3, cap: int = 40) -> list[dict]:
    found: list[tuple[float, Path]] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        if len(d.parts) - base_depth >= depth:
            dirnames.clear()
        dirnames[:] = [n for n in dirnames if not n.startswith((".", "__"))
                       and n != "node_modules"]
        for fn in filenames:
            if Path(fn).suffix.lower() in IMAGE_EXTS:
                p = d / fn
                with contextlib.suppress(OSError):
                    found.append((p.stat().st_mtime, p))
    found.sort(reverse=True)
    return [
        {"path": str(p), "name": p.name, "rel": str(p.relative_to(root)),
         "mtime": int(mt)}
        for mt, p in found[:cap]
    ]


def _default_plot_root(name: str) -> Path | None:
    rows = sessions.list_sessions(with_preview=False)
    row = next((r for r in rows if r["name"] == name), None)
    if row is None or not row["cwd"]:
        return None
    return Path(row["cwd"])


async def api_plots(request: web.Request) -> web.Response:
    name = request.query.get("session", "")
    default = await asyncio.to_thread(_default_plot_root, name)
    override = _read_state("plotroots.json").get(name)
    root = Path(override) if override else default
    resp = {"images": [], "root": str(root) if root else "",
            "default_root": str(default) if default else "",
            "override": bool(override)}
    if root is None or not root.is_dir():
        return web.json_response(resp)
    resp["images"] = await asyncio.to_thread(_find_images, root)
    return web.json_response(resp)


async def api_plotroot_put(request: web.Request) -> web.Response:
    name = request.match_info["session"]
    body = await request.json()
    raw = str(body.get("root", "")).strip()
    roots = _read_state("plotroots.json")
    if not raw:  # empty -> back to the thread's cwd
        roots.pop(name, None)
        _write_state("plotroots.json", roots)
        return web.json_response({"ok": True, "override": False})
    root = Path(raw).expanduser().resolve()
    if not root.is_dir() or not root.is_relative_to(Path.home().resolve()):
        raise web.HTTPBadRequest(text="plot root must be a directory under $HOME")
    roots[name] = str(root)
    _write_state("plotroots.json", roots)
    return web.json_response({"ok": True, "override": True})


async def api_file(request: web.Request) -> web.FileResponse:
    raw = request.query.get("path", "")
    path = Path(raw).resolve()
    home = Path.home().resolve()
    ok = path.is_file() and path.suffix.lower() in IMAGE_EXTS
    if not ok or not path.is_relative_to(home):
        raise web.HTTPForbidden(text="only images under $HOME are served")
    return web.FileResponse(path)


# --- app --------------------------------------------------------------------- #

def build_app(token: str | None = None) -> web.Application:
    app = web.Application(middlewares=[auth])
    app["token"] = token or load_token()
    app.router.add_get("/", page)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_get("/api/config", api_config)
    app.router.add_post("/api/threads", api_thread_create)
    app.router.add_post("/api/threads/{session}/rename", api_thread_rename)
    app.router.add_delete("/api/threads/{session}", api_thread_delete)
    app.router.add_get("/api/plots", api_plots)
    app.router.add_put("/api/plotroot/{session}", api_plotroot_put)
    app.router.add_put("/api/order", api_order_put)
    app.router.add_get("/api/file", api_file)
    app.router.add_get("/api/notes/{session}", api_notes_get)
    app.router.add_put("/api/notes/{session}", api_notes_put)
    app.router.add_get("/ws", ws_terminal)
    app.router.add_static("/static/", STATIC)
    return app
