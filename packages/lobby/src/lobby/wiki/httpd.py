"""The lobby wiki server: a public file tree with token-gated state push.

This file is deliberately standalone (stdlib only, no lobby imports): the
provisioner ships it to the pod as a single file and runs it with a bare
``python3 wiki_server.py``. The content model is just a directory tree under
$WIKI_DATA — clients pull the whole tree, change arbitrary files, and push it
back. The server renders it browsably:

    GET  /                    index: listing of the tree root
    GET  /<path>              .md file -> rendered page (?raw for the source);
                              dir -> its index.html/index.md, else a listing;
                              anything else -> raw bytes
    GET  /api/ping            liveness + entry count
    GET  /api/state           tar.gz of the whole tree
    POST /api/state           tar.gz body, atomically replaces the tree   (Bearer token)

Dotfile path segments are hidden from listings and 404 when fetched.
"""

from __future__ import annotations

import hmac
import html
import io
import json
import mimetypes
import os
import re
import shutil
import tarfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

MARKED_CDN = "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"

_STYLE = """
:root { color-scheme: light dark; --fg: #1a1a1a; --muted: #777; --bg: #fafafa;
        --card: #fff; --border: #e2e2e2; --accent: #2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e8e8e8; --muted: #999; --bg: #111; --card: #1b1b1b;
          --border: #333; --accent: #60a5fa; }
}
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 60rem; padding: 2rem 1.25rem; background: var(--bg);
       color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
h1 { font-size: 1.3rem; margin: 0 0 .25rem; }
.sub { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; overflow-wrap: anywhere; }
.crumb { font-size: .85rem; margin-bottom: 1.5rem; }
.crumb a { color: var(--muted); text-decoration: none; }
.crumb a:hover { color: var(--accent); }
table.ls { border-collapse: collapse; width: 100%; }
table.ls td { padding: .35rem .75rem .35rem 0; border-bottom: 1px solid var(--border); }
table.ls a { color: var(--accent); text-decoration: none; overflow-wrap: anywhere; }
table.ls a:hover { text-decoration: underline; }
.kind { display: inline-block; font-size: .7rem; border: 1px solid var(--border);
        border-radius: .6rem; padding: 0 .5rem; color: var(--muted); }
.meta { color: var(--muted); font-size: .8rem; white-space: nowrap; text-align: right; }
.empty { color: var(--muted); }
"""

_MD_STYLE = """
body { max-width: 46rem; }
#content h1 { font-size: 1.6rem; }
#content h1, #content h2, #content h3 { margin: 1.6em 0 .5em; line-height: 1.25; font-size: revert; }
#content h1:first-child { margin-top: 0; }
#content pre { background: var(--card); border: 1px solid var(--border); border-radius: .5rem;
               padding: .8rem 1rem; overflow-x: auto; font-size: .85em; }
#content code { font-family: ui-monospace, monospace; font-size: .92em; }
#content :not(pre) > code { background: var(--card); border: 1px solid var(--border);
                            border-radius: .25rem; padding: 0 .25em; }
#content img { max-width: 100%; }
#content table { border-collapse: collapse; display: block; overflow-x: auto; }
#content th, #content td { border: 1px solid var(--border); padding: .3rem .6rem; }
#content blockquote { border-left: 3px solid var(--border); margin: 0; padding: 0 1rem;
                      color: var(--muted); }
#content a { color: var(--accent); }
"""


def data_dir() -> Path:
    d = Path(os.environ.get("WIKI_DATA") or "/data/wiki")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token() -> str:
    return os.environ.get("WIKI_TOKEN") or ""


def _hidden(rel: Path) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def resolve(path: str) -> Path | None:
    """Map a URL path to a file under the data dir; None if it escapes or is hidden."""
    base = data_dir().resolve()
    target = (base / path.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        return None
    if _hidden(target.relative_to(base)):
        return None
    return target


def visible_entries(directory: Path) -> list[Path]:
    return sorted(
        (p for p in directory.iterdir() if not p.name.startswith(".")),
        key=lambda p: (p.is_file(), p.name),
    )


def tree_size() -> int:
    return sum(1 for p in data_dir().rglob("*")
               if p.is_file() and not _hidden(p.relative_to(data_dir())))


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract only plain files/dirs with paths that stay inside ``dest``."""
    for member in tar.getmembers():
        name = member.name
        if name.startswith(("/", "..")) or ".." in Path(name).parts:
            raise ValueError(f"unsafe path in archive: {name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported member type in archive: {name!r}")
    tar.extractall(dest)


def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


class WikiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lobby-wiki"
    site_title = os.environ.get("WIKI_TITLE") or "wiki"

    def log_message(self, fmt, *args):  # noqa: N802 - stdlib name
        pass

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        path, query = unquote(parts.path), parts.query
        if path == "/api/ping":
            return self._json({"app": "lobby-wiki", "files": tree_size(),
                               "writable": bool(_token())})
        if path == "/api/state":
            return self._state_get()
        target = resolve(path)
        if target is None or not target.exists():
            return self._error(404, "not found")
        if target.is_dir():
            if not path.endswith("/"):
                return self._redirect(path + "/")
            return self._dir(path, target)
        if target.suffix.lower() in (".md", ".markdown") and query != "raw":
            return self._md_page(path, target)
        self._file(target, raw=bool(query == "raw"))

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        if urlsplit(self.path).path == "/api/state":
            return self._state_post()
        self._error(404, "not found")

    # -- reading -----------------------------------------------------------

    def _file(self, target: Path, raw: bool = False):
        ctype = "text/plain" if raw else (
            mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _dir(self, url_path: str, directory: Path):
        for entry in ("index.html", "index.md"):
            if (directory / entry).is_file():
                if entry.endswith(".md"):
                    return self._md_page(url_path, directory / entry)
                return self._file(directory / entry)
        return self._listing(url_path, directory)

    def _listing(self, url_path: str, directory: Path):
        rows = []
        for p in visible_entries(directory):
            href = quote(p.name) + ("/" if p.is_dir() else "")
            kind = "dir" if p.is_dir() else (p.suffix.lstrip(".") or "file")
            mtime = ago(p.stat().st_mtime)
            rows.append(
                f'<tr><td><a href="{href}">{html.escape(p.name)}</a></td>'
                f'<td><span class="kind">{html.escape(kind)}</span></td>'
                f'<td class="meta">{html.escape(mtime)}</td></tr>'
            )
        body = (f'<table class="ls">{"".join(rows)}</table>' if rows
                else '<p class="empty">Empty — push something with lobby.wiki.</p>')
        title = html.escape(self.site_title if url_path == "/" else url_path.strip("/"))
        crumb = "" if url_path == "/" else '<div class=crumb><a href="/">← index</a></div>'
        self._html(200, (
            "<!doctype html><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{_STYLE}</style>"
            f"{crumb}<h1>{title}</h1>"
            f"<div class=sub>{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}</div>{body}"
        ))

    def _md_page(self, url_path: str, target: Path):
        try:
            text = target.read_text()
        except OSError:
            return self._error(404, "not found")
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = html.escape(m.group(1).strip() if m else target.stem)
        # </ would close the script tag early even inside a JS string literal
        md_json = json.dumps(text).replace("</", "<\\/")
        self._html(200, (
            "<!doctype html><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{_STYLE}{_MD_STYLE}</style>"
            f'<div class=crumb><a href="/">← {html.escape(self.site_title)}</a></div>'
            "<div id=content><pre></pre></div>"
            f'<script src="{MARKED_CDN}"></script>'
            f"<script>const md = {md_json};\n"
            "const el = document.getElementById('content');\n"
            "if (window.marked) { el.innerHTML = marked.parse(md); }\n"
            "else { el.firstChild.textContent = md; }\n"
            "</script>"
        ))

    def _state_get(self):
        buf = io.BytesIO()
        base = data_dir()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(base)
                if not _hidden(rel):
                    tar.add(p, arcname=str(rel), recursive=False)
        self._send(200, buf.getvalue(), "application/gzip")

    # -- writing -----------------------------------------------------------

    def _authed(self) -> bool:
        token = _token()
        if not token:
            return False
        got = self.headers.get("Authorization") or ""
        return hmac.compare_digest(got, f"Bearer {token}")

    def _state_post(self):
        # Drain the body before any error response, or the client sees a
        # connection reset mid-upload instead of the 4xx.
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self._authed():
            return self._error(401, "missing or bad bearer token")
        base = data_dir()
        incoming = base.parent / f".{base.name}-incoming"
        outgoing = base.parent / f".{base.name}-old"
        shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                _safe_extract(tar, incoming)
        except (tarfile.TarError, ValueError, OSError) as e:
            shutil.rmtree(incoming, ignore_errors=True)
            return self._error(400, f"bad archive: {e}")
        shutil.rmtree(outgoing, ignore_errors=True)
        base.replace(outgoing)
        incoming.replace(base)
        shutil.rmtree(outgoing, ignore_errors=True)
        self._json({"files": tree_size()})

    # -- response helpers ----------------------------------------------------

    def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
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
        if self.path.startswith("/api/"):
            return self._json({"error": message}, code)
        self._html(code, f"<!doctype html><title>{code}</title><style>{_STYLE}</style>"
                         f"<h1>{code}</h1><p>{html.escape(message)}</p>"
                         '<p><a href="/">← back to the index</a></p>')

    def _redirect(self, location: str):
        self._send(302, b"", "text/plain", {"Location": quote(location)})


def main() -> None:
    port = int(os.environ.get("WIKI_PORT") or 8080)
    data_dir()  # fail fast if the volume is not writable
    if not _token():
        print("wiki: WIKI_TOKEN not set — the wiki is read-only", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), WikiHandler)
    print(f"wiki: serving {data_dir()} on 0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
