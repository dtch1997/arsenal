"""Notes — the deliberate push channel onto threads.

Everything else in this package is *observed* (scan reads transcripts after
the fact). A note is the opposite: an agent or Daniel, mid-session, decides
"I'm moving on — park this durably" and pushes a context-dump onto a thread.
Notes are markdown files in the spool (``~/.threads/notes/<slug>/<stamp>.md``)
with a small ``key: value`` frontmatter block — greppable, human-editable,
never committed.

The slug does not have to exist in the registry: a note onto an unknown slug
*seeds* a thread (rendered with a ``new`` marker until a stub exists), per the
bottom-up draft-and-veto philosophy. Like the rest of the spool, this module
never writes into ``~/jarvis-memory``.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config

# frontmatter keys, in write order
_KEYS = ("slug", "title", "status", "created", "session_id", "cwd", "branch")


def _git_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch = out.stdout.strip()
    return branch if out.returncode == 0 and branch and branch != "HEAD" else None


def add_note(slug: str, body: str, *, title: str | None = None,
             status: str | None = None, session_id: str | None = None,
             cwd: str | None = None, branch: str | None = None,
             now: datetime | None = None) -> Path:
    """Write one note onto ``slug`` and return its path.

    cwd/branch/session_id are captured from the environment when not given, so
    a bare ``threads note <slug> "..."`` from inside a session records enough
    to find the way back (session_id comes from ``CLAUDE_SESSION_ID`` if the
    harness exports it; absent otherwise).
    """
    slug = slug.strip().lower()
    if not slug or any(c in slug for c in "/\\ "):
        raise ValueError(f"bad slug: {slug!r} (use a kebab-case memory-stub name)")
    body = body.strip()
    if not body:
        raise ValueError("empty note body")
    now = now or datetime.now(timezone.utc)
    cwd = cwd or os.getcwd()
    rec = {
        "slug": slug,
        "title": title or body.splitlines()[0].lstrip("# ").strip()[:80],
        "status": status or "",
        "created": now.isoformat(timespec="seconds"),
        "session_id": session_id or os.environ.get("CLAUDE_SESSION_ID", ""),
        "cwd": cwd,
        "branch": branch or _git_branch(cwd) or "",
    }
    d = config.notes_dir() / slug
    d.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{stamp}.md"
    n = 1
    while path.exists():
        path = d / f"{stamp}-{n}.md"
        n += 1
    front = "\n".join(f"{k}: {rec[k]}" for k in _KEYS if rec[k])
    path.write_text(f"---\n{front}\n---\n\n{body}\n")
    return path


def _parse_note(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    rec = {k: "" for k in _KEYS}
    body = text
    if text.startswith("---\n"):
        head, sep, rest = text[4:].partition("\n---\n")
        if sep:
            body = rest.strip()
            for line in head.splitlines():
                k, _, v = line.partition(":")
                if k.strip() in _KEYS:
                    rec[k.strip()] = v.strip()
    rec["slug"] = rec["slug"] or path.parent.name
    rec["body"] = body.strip()
    rec["path"] = str(path)
    return rec


def load_notes(slug: str | None = None) -> list[dict]:
    """All notes (or one slug's), newest first."""
    base = config.notes_dir()
    dirs = [base / slug] if slug else sorted(base.iterdir()) if base.is_dir() else []
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            rec = _parse_note(p)
            if rec:
                out.append(rec)
    out.sort(key=lambda r: r.get("created", ""), reverse=True)
    return out
