"""The lobby hub daemon: index page + reverse proxy + registration API, one tunnel."""

from __future__ import annotations

import html
import http.client
import json
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import state

# End-to-end headers only; hop-by-hop headers must not be forwarded (RFC 9110 §7.6.1).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_APP_RE = re.compile(r"^/a/([a-z0-9][a-z0-9-]*)(/.*)?$")

_STYLE = """
:root { color-scheme: light dark; --fg: #1a1a1a; --muted: #777; --bg: #fafafa;
        --card: #fff; --border: #e2e2e2; --accent: #2563eb; --dead: #b91c1c; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e8e8e8; --muted: #999; --bg: #111; --card: #1b1b1b;
          --border: #333; --accent: #60a5fa; --dead: #f87171; }
}
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 60rem; padding: 2rem 1.25rem; background: var(--bg);
       color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .08em;
     color: var(--muted); margin: 2rem 0 .75rem; }
.sub { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; overflow-wrap: anywhere; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr)); gap: .75rem; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: .5rem;
        padding: .8rem .9rem; }
.card a.name { font-weight: 600; color: var(--accent); text-decoration: none;
               overflow-wrap: anywhere; }
.card a.name:hover { text-decoration: underline; }
.kind { display: inline-block; font-size: .7rem; border: 1px solid var(--border);
        border-radius: .6rem; padding: 0 .5rem; color: var(--muted); margin-left: .35rem;
        vertical-align: 2px; }
.title { margin: .25rem 0 .35rem; font-size: .85rem; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: .75rem; }
.ended { opacity: .55; }
.ended .name { color: var(--dead); }
.empty { color: var(--muted); }
"""


class HubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lobby"
    # Set by run() once the hub (and its tunnel, if any) is up.
    public_base: str | None = None
    ready = False

    def log_message(self, fmt, *args):  # noqa: N802 - stdlib name
        pass  # keep hub.log for lifecycle events, not per-request noise

    # One handler for every method; routing decides what is allowed.
    def do_GET(self):  # noqa: N802
        self._route()

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET

    # -- routing ---------------------------------------------------------

    def _route(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parts.query
        m = _APP_RE.match(path)
        if m:
            name, rest = m.group(1), m.group(2)
            if rest is None:  # /a/<name> -> /a/<name>/ so relative URLs resolve
                loc = f"/a/{name}/" + (f"?{query}" if query else "")
                return self._redirect(loc)
            return self._proxy(name, rest, query)
        if path == "/":
            return self._index()
        if path == "/api/ping":
            return self._json(
                {"app": "lobby", "ready": self.ready, "url": self.public_base,
                 "pid": os.getpid()}
            )
        if path == "/api/apps":
            apps = [dict(a, live=state.app_live(a)) for a in state.list_apps()]
            return self._json({"apps": apps})
        if path == "/api/register" and self.command == "POST":
            return self._register()
        if path == "/api/unregister" and self.command == "POST":
            return self._unregister()
        self._error(404, "not found")

    # -- registration ----------------------------------------------------

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _register(self):
        try:
            body = self._body()
            port = int(body["port"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return self._error(400, "register needs JSON with at least {name, port}")
        name = base = state.slugify(str(body.get("name") or f"app-{port}"))
        n = 2
        while True:
            existing = state.read_json(state.app_path(name))
            same_app = existing and (existing["port"] == port or not state.app_live(existing))
            if existing is None or same_app:
                break
            name = f"{base}-{n}"
            n += 1
        rec = {
            "name": name,
            "port": port,
            "kind": str(body.get("kind") or "app"),
            "title": body.get("title"),
            "cwd": body.get("cwd"),
            "pid": body.get("pid"),
            "started_at": float(body.get("started_at") or time.time()),
        }
        state.write_json(state.app_path(name), rec)
        base_url = (self.public_base or "").rstrip("/")
        self._json({"name": name, "path": f"/a/{name}/", "url": f"{base_url}/a/{name}/"})

    def _unregister(self):
        try:
            name = state.slugify(self._body()["name"])
        except (KeyError, json.JSONDecodeError):
            return self._error(400, "unregister needs JSON with {name}")
        state.app_path(name).unlink(missing_ok=True)
        self._json({"removed": name})

    # -- reverse proxy ---------------------------------------------------

    def _proxy(self, name: str, rest: str, query: str):
        app = state.read_json(state.app_path(name))
        if app is None:
            return self._error(404, f"no app registered as {name!r}")
        path = rest + (f"?{query}" if query else "")
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
        }
        headers["Host"] = f"127.0.0.1:{app['port']}"
        headers["Connection"] = "close"
        conn = http.client.HTTPConnection("127.0.0.1", app["port"], timeout=60)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError:
            return self._error(
                502,
                f"app {name!r} is not responding on port {app['port']} "
                "(it may have exited; see the index for live apps)",
            )
        finally:
            conn.close()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in _HOP_BY_HOP or lk == "content-length":
                continue
            # Root-absolute redirects from the backend must stay inside the mount.
            if lk == "location" and v.startswith("/"):
                v = f"/a/{name}{v}"
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # -- index -----------------------------------------------------------

    def _index(self):
        live, ended = [], []
        for app in state.list_apps():
            (live if state.app_live(app) else ended).append(app)
        live.sort(key=lambda a: -(a.get("started_at") or 0))
        ended.sort(key=lambda a: -(a.get("started_at") or 0))
        base = html.escape(self.public_base or "")

        def card(app: dict, dead: bool) -> str:
            name = html.escape(app["name"])
            kind = html.escape(app.get("kind") or "app")
            title = html.escape(app.get("title") or "")
            meta = f"port {app['port']} · started {state.ago(app.get('started_at'))}"
            cls = "card ended" if dead else "card"
            return (
                f'<div class="{cls}"><a class="name" href="/a/{name}/">{name}</a>'
                f'<span class="kind">{kind}</span>'
                f'<div class="title">{title}</div>'
                f'<div class="meta">{html.escape(meta)}</div></div>'
            )

        def section(title: str, apps: list[dict], dead: bool) -> str:
            if not apps:
                return ""
            cards = "".join(card(a, dead) for a in apps)
            return f"<h2>{title}</h2><div class=grid>{cards}</div>"

        body = section("Live", live, False) + section("Ended", ended, True)
        if not body:
            body = '<p class="empty">Nothing here yet — serve something with lobby.serve(port, name=...).</p>'
        page = (
            "<!doctype html><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width, initial-scale=1">'
            "<meta http-equiv=refresh content=10>"
            f"<title>lobby</title><style>{_STYLE}</style>"
            f"<h1>lobby</h1><div class=sub>{base or 'local only'} · "
            f"{len(live)} live · {len(ended)} ended</div>{body}"
        )
        self._html(200, page)

    # -- response helpers --------------------------------------------------

    def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _html(self, code: int, text: str):
        self._send(code, text.encode(), "text/html; charset=utf-8")

    def _json(self, obj: dict, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _error(self, code: int, message: str):
        self._html(code, f"<!doctype html><title>lobby: {code}</title><style>{_STYLE}</style>"
                         f"<h1>{code}</h1><p>{html.escape(message)}</p>"
                         '<p><a href="/">← back to the lobby</a></p>')

    def _redirect(self, location: str):
        self._send(301, b"", "text/plain", {"Location": location})


def run(port: int = state.DEFAULT_PORT, tunnel: bool = True) -> None:
    """Run the hub in the foreground (the CLI's hidden `_daemon` command lands here)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), HubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"lobby: hub on http://127.0.0.1:{port}", flush=True)

    public = f"http://127.0.0.1:{port}"
    tunnel_stop = None
    if tunnel:
        try:
            import marquee

            public, tunnel_stop = marquee.tunnel(port)
            public = public.rstrip("/")
            print(f"lobby: tunnel up at {public}", flush=True)
        except Exception as e:  # missing marquee/cloudflared, tunnel timeout, ...
            print(f"lobby: tunnel unavailable ({e!r}); serving locally only", flush=True)

    HubHandler.public_base = public
    HubHandler.ready = True
    state.write_json(
        state.hub_path(),
        {"pid": os.getpid(), "port": port, "url": public, "started_at": time.time()},
    )

    stopping = threading.Event()

    def _shutdown(signum, frame):
        stopping.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    while not stopping.wait(1.0):
        pass
    print("lobby: shutting down", flush=True)
    if tunnel_stop:
        try:
            tunnel_stop()
        except Exception:
            pass
    server.shutdown()
    sys.exit(0)
