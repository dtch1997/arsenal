"""The editor HTTP server.

A small custom handler (rather than http.server's static one) because we need
the write-back: GET / serves the editor page, GET /<asset> serves figures and
other files from the draft's own directory (with path-traversal blocked),
POST /save atomically writes the edited Markdown to disk and returns freshly
rendered HTML for the preview, and POST /revert restores the draft to its
last-committed (git HEAD) state.

Because cowrite is a CO-writing tool, the draft on disk can change under the
editor at any time (the AI keeps writing between human saves). Every response
therefore carries a `rev` — a hash of the draft's current on-disk content —
and `/save` refuses (409) to write over a disk state the client hasn't seen,
instead of silently reverting the other writer's work. `GET /api/state` is the
cheap poll the page uses to notice external edits; `GET /api/doc` fetches the
full document to refresh from.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .render import analyze_comments, build_page, render_fragment


def content_rev(text: str) -> str:
    """Revision id for a draft state: a short content hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# A git revision as it may arrive from the client. `git show <sha>:<path>`
# runs via subprocess, so the sha reaches git as an argument — accept only
# hex object names (and the literal HEAD) so request input can never inject
# git flags or a `:`/path of its own.
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,64}")


def _valid_sha(sha: str) -> bool:
    return bool(_SHA_RE.fullmatch(sha))


def _git_locate(draft: Path) -> tuple[str | None, str | None, str | None]:
    """Resolve the draft to (repo_toplevel, rel_posix, None), or (None, None,
    reason) if it isn't inside a git work tree. The reason strings are the same
    ones the revert path has always surfaced, so every git-backed feature
    degrades with one friendly voice."""
    draft = draft.resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(draft.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None, None, "git is not installed"
    if top.returncode != 0:
        return None, None, "not inside a git repository"
    toplevel = Path(top.stdout.strip())
    try:
        rel = draft.relative_to(toplevel)
    except ValueError:
        return None, None, "draft is outside the git work tree"
    return str(toplevel), rel.as_posix(), None


def git_version_text(draft: Path, sha: str = "HEAD") -> tuple[str | None, str | None]:
    """Return (committed_text, None) for the draft's content at a git revision
    (`git show <sha>:<rel>`), or (None, reason) if it can't be obtained (not a
    repo, file untracked at that revision, etc.). `sha` must be a hex object
    name or the literal 'HEAD'."""
    if sha != "HEAD" and not _valid_sha(sha):
        return None, "invalid revision"
    toplevel, rel, reason = _git_locate(draft)
    if reason is not None:
        return None, reason
    show = subprocess.run(
        ["git", "-C", toplevel, "show", f"{sha}:{rel}"],
        capture_output=True, text=True,
    )
    if show.returncode != 0:
        if sha == "HEAD":
            return None, "draft is not committed at HEAD"
        return None, f"version {sha[:8]} of this draft is unavailable"
    return show.stdout, None


def git_head_text(draft: Path) -> tuple[str | None, str | None]:
    """The draft's content at git HEAD — the degenerate `git_version_text`."""
    return git_version_text(draft, "HEAD")


def git_log(draft: Path, limit: int = 50) -> tuple[list[dict] | None, str | None]:
    """Return (commits, None) — the commits touching the draft, newest first,
    capped at `limit` — or (None, reason) if the draft isn't in a git work tree.
    An in-repo-but-never-committed draft yields an empty list (no reason)."""
    toplevel, rel, reason = _git_locate(draft)
    if reason is not None:
        return None, reason
    # %x1f is the ASCII unit separator — a field delimiter that can't appear in
    # a commit subject, so the split below is unambiguous.
    log = subprocess.run(
        ["git", "-C", toplevel, "log", f"-n{limit}",
         "--format=%H%x1f%cI%x1f%s", "--", rel],
        capture_output=True, text=True,
    )
    if log.returncode != 0:
        return None, "could not read git history for this draft"
    commits = []
    for line in log.stdout.splitlines():
        if not line:
            continue
        sha, date, subject = line.split("\x1f", 2)
        commits.append({"sha": sha, "date": date, "subject": subject})
    return commits, None


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

    def read_draft() -> str:
        return draft.read_text(encoding="utf-8", errors="replace") if draft.exists() else ""

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

        def _send_json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                md = read_draft()
                # Comment marker author (Docs-style review); defaults to "daniel".
                author = parse_qs(urlparse(self.path).query).get("author", ["daniel"])[0]
                self._send(200, build_page(md, title, str(draft), content_rev(md), author).encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if path == "/api/raw":
                self._send(200, read_draft().encode("utf-8"), "text/plain; charset=utf-8")
                return
            if path == "/api/state":
                self._send_json(200, {"rev": content_rev(read_draft())})
                return
            if path == "/api/doc":
                md = read_draft()
                self._send_json(200, {"md": md, "html": render_fragment(md),
                                      "rev": content_rev(md), **analyze_comments(md)})
                return
            if path == "/api/history":
                commits, err = git_log(draft)
                if err is not None:
                    self._send_json(409, {"ok": False, "error": err})
                    return
                self._send_json(200, {"ok": True, "commits": commits})
                return
            if path == "/api/version":
                sha = (parse_qs(urlparse(self.path).query).get("sha") or [""])[0]
                if not _valid_sha(sha):
                    self._send_json(400, {"ok": False, "error": "invalid sha"})
                    return
                md, err = git_version_text(draft, sha)
                if err is not None:
                    self._send_json(409, {"ok": False, "error": err})
                    return
                self._send_json(200, {"ok": True, "sha": sha, "md": md,
                                      "html": render_fragment(md)})
                return
            asset = safe_asset(self.path)
            if asset is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(str(asset))[0] or "application/octet-stream"
            self._send(200, asset.read_bytes(), ctype)

        def _write_draft(self, md: str) -> None:
            # Atomic write: temp file in the same dir, then replace, so a
            # save can never truncate the draft midway.
            tmp = draft.with_suffix(draft.suffix + ".tmp")
            tmp.write_text(md, encoding="utf-8")
            os.replace(tmp, draft)

        def _saved_response(self, md: str) -> dict:
            return {
                "ok": True,
                "html": render_fragment(md),
                "saved": md,
                "rev": content_rev(md),
                "at": datetime.now().strftime("%H:%M:%S"),
                **analyze_comments(md),  # refresh the review bubbles after a save
            }

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/render":
                # Typing-time preview: render markdown to a fragment WITHOUT
                # writing to disk, using the same render_fragment as the save
                # path so the live preview matches what a save would land.
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    md = self.rfile.read(n).decode("utf-8", errors="replace")
                    self._send_json(200, {"ok": True, "html": render_fragment(md)})
                except Exception as e:
                    self._send_json(500, {"ok": False, "error": str(e)})
                return
            if path == "/save":
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    md = self.rfile.read(n).decode("utf-8", errors="replace")
                    # Optimistic concurrency: the client says which disk state
                    # (rev) its edit is based on. If the draft changed on disk
                    # since — the AI wrote to it — refuse rather than silently
                    # revert that work; the client resolves and retries.
                    base = self.headers.get("X-Base-Rev")
                    disk = read_draft()
                    if base is not None and base != content_rev(disk):
                        self._send_json(409, {
                            "ok": False, "conflict": True,
                            "rev": content_rev(disk), "disk": disk,
                            "error": "draft changed on disk since you last synced",
                        })
                        return
                    self._write_draft(md)
                    self._send_json(200, self._saved_response(md))
                except Exception as e:  # surface to the browser status line
                    self._send_json(500, {"ok": False, "error": str(e)})
                return
            if path == "/revert":
                try:
                    md, err = git_head_text(draft)
                    if err is not None:
                        self._send_json(409, {"ok": False, "error": err})
                        return
                    self._write_draft(md)
                    self._send_json(200, self._saved_response(md))
                except Exception as e:
                    self._send_json(500, {"ok": False, "error": str(e)})
                return
            self._send(404, b'{"ok":false,"error":"unknown endpoint"}', "application/json")

    return Handler


def run_server(file: str, port: int, title: str | None = None) -> None:
    """Run the editor server in the foreground (blocks). Launched detached by serve()."""
    draft = Path(file).resolve()
    handler = make_handler(draft, title or draft.stem)
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
