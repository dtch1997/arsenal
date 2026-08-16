"""``desk serve`` — serve the inbox through the shared lobby hub, re-rendering
on an interval. Falls back to a plain localhost HTTP server if lobby is
unavailable (and says so), mirroring how databrowser degrades.
"""

from __future__ import annotations

import html
import http.server
import socket
import sys
import threading
from dataclasses import dataclass, field

from .core import inbox_path, render


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _page() -> bytes:
    try:
        md = inbox_path().read_text()
    except OSError:
        md = "# desk\n\n_inbox not yet rendered_"
    body = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>desk — waiting on Daniel</title>"
        f"<meta http-equiv='refresh' content='60'>"
        f"<style>body{{font:14px/1.5 ui-monospace,monospace;max-width:52rem;"
        f"margin:2rem auto;padding:0 1rem}}pre{{white-space:pre-wrap}}</style>"
        f"</head><body><pre>{html.escape(md)}</pre></body></html>"
    )
    return body.encode()


@dataclass
class DeskServer:
    local_url: str
    url: str
    httpd: http.server.ThreadingHTTPServer
    _stop: threading.Event = field(default_factory=threading.Event)
    hub_name: str | None = None

    @property
    def alive(self) -> bool:
        return not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        if self.hub_name:
            try:
                import lobby
                lobby.unregister(self.hub_name)
            except Exception:
                pass
        self.httpd.shutdown()
        self.httpd.server_close()


def serve(*, interval: int = 60, port: int | None = None, tunnel: bool = True,
          gh_runner=None) -> DeskServer:
    """Render the inbox, serve it, and re-render every ``interval`` seconds."""
    render(gh_runner=gh_runner)  # first render before anyone connects

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            payload = _page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    port = port or _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    local_url = f"http://127.0.0.1:{port}"

    srv = DeskServer(local_url=local_url, url=local_url, httpd=httpd)

    # background re-render loop
    def _loop():
        while not srv._stop.wait(interval):
            try:
                render(gh_runner=gh_runner)
            except Exception as e:  # never let the refresh thread kill serving
                print(f"desk: re-render failed ({e})", file=sys.stderr)

    threading.Thread(target=_loop, daemon=True).start()

    if not tunnel:
        return srv

    try:
        import lobby
        public_url = lobby.serve(
            port, name="desk", kind="desk", title="desk — waiting on Daniel",
        )
        srv.url = public_url
        srv.hub_name = public_url.rstrip("/").rsplit("/a/", 1)[-1]
    except Exception as e:
        print(f"note: lobby hub unavailable ({e}); serving locally only.",
              file=sys.stderr)
    return srv
