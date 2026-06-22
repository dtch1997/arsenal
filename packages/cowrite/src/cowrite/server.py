"""The editor HTTP server.

A small custom handler (rather than http.server's static one) because we need
the write-back: GET / serves the editor page, GET /<asset> serves figures and
other files from the draft's own directory (with path-traversal blocked), and
POST /save atomically writes the edited Markdown to disk and returns freshly
rendered HTML for the preview.
"""

from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .render import build_page, render_fragment


def make_handler(draft: Path, title: str):
    root = draft.parent  # figures / assets live alongside the draft

    def safe_asset(req_path: str) -> Path | None:
        rel = unquote(urlparse(req_path).path).lstrip("/")
        if not rel:
            return None
        target = (root / rel).resolve()
        try:
            target.relative_to(root)  # block path traversal outside the draft dir
        except ValueError:
            return None
        return target if target.is_file() else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet; the launcher keeps its own log file
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                md = draft.read_text(encoding="utf-8", errors="replace") if draft.exists() else ""
                self._send(200, build_page(md, title, str(draft)).encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path == "/api/raw":
                md = draft.read_text(encoding="utf-8", errors="replace") if draft.exists() else ""
                self._send(200, md.encode("utf-8"), "text/plain; charset=utf-8")
                return
            asset = safe_asset(self.path)
            if asset is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(str(asset))[0] or "application/octet-stream"
            self._send(200, asset.read_bytes(), ctype)

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                self._send(404, b'{"ok":false,"error":"unknown endpoint"}', "application/json")
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                md = self.rfile.read(n).decode("utf-8", errors="replace")
                # Atomic write: temp file in the same dir, then replace, so a
                # save can never truncate the draft midway.
                tmp = draft.with_suffix(draft.suffix + ".tmp")
                tmp.write_text(md, encoding="utf-8")
                os.replace(tmp, draft)
                resp = {
                    "ok": True,
                    "html": render_fragment(md),
                    "saved": md,
                    "at": datetime.now().strftime("%H:%M:%S"),
                }
                self._send(200, json.dumps(resp).encode("utf-8"), "application/json")
            except Exception as e:  # surface to the browser status line
                self._send(500, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")

    return Handler


def run_server(file: str, port: int, title: str | None = None) -> None:
    """Run the editor server in the foreground (blocks). Launched detached by serve()."""
    draft = Path(file).resolve()
    handler = make_handler(draft, title or draft.stem)
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
