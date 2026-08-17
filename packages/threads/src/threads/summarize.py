"""Summarize one session with a single headless ``claude -p`` call.

The subprocess is a **seam**: :func:`default_runner` shells out to ``claude``,
but every caller takes a ``runner`` argument so tests inject canned JSON and no
real model is ever called. A runner is ``runner(prompt, *, model) -> dict`` with
keys ``text`` (the model's raw reply, expected to be JSON) and ``cost_usd``.

The output record is JSON-schema-shaped (see :data:`RECORD_FIELDS`); we parse
the model reply leniently (stripping ``` fences), coerce every field to its
expected type, and never let a bad reply crash the sweep.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from . import config
from .transcripts import Session

# The summarizer shells out to `claude`, which itself writes a session
# transcript into `$CLAUDE_CONFIG_DIR/projects` (default `~/.claude/projects`).
# If that landed in the scanned tree, scanning would *create* sessions to scan
# (a feedback loop) and `scan --check` could never be a no-op. Redirect the
# subprocess's config dir to an isolated, stable location so its reflections
# never pollute the corpus.
_ISOLATED_CONFIG_DIR = os.path.join(tempfile.gettempdir(), "threads-claude")

STATUS_SIGNALS = ("wrapped-up", "blocked", "abandoned-midstream", "ongoing")

RECORD_FIELDS = (
    "title", "summary", "artifacts", "status_signals", "candidate_slugs",
    "keywords",
)

_SCHEMA_HINT = """\
Return ONLY a JSON object (no prose, no code fence) with exactly these keys:
  "title": string, one line naming what this session was about
  "summary": string, 3-6 sentences: what was attempted, what happened, how it ended
  "artifacts": object with keys "branches","prs","files","urls", each a list of strings
  "prs" entries look like "owner/repo#123"; "branches" are git branch names
  "status_signals": list, subset of ["wrapped-up","blocked","abandoned-midstream","ongoing"]
  "candidate_slugs": list of thread slugs from the REGISTRY below this session
       plausibly belongs to (may be empty; only use slugs shown in the index)
  "keywords": list of 3-8 short topical keywords
"""


def build_prompt(session: Session, registry_slugs: list[str]) -> str:
    index = "\n".join(f"- {s}" for s in registry_slugs)
    return (
        "You are summarizing a Claude Code session transcript for an activity "
        "dashboard. Be concise and factual.\n\n"
        f"{_SCHEMA_HINT}\n"
        "THREAD REGISTRY (slug: description) — pick candidate_slugs only from here:\n"
        f"{index}\n\n"
        f"SESSION cwd={session.cwd or '?'} branch={session.git_branch or '?'}\n"
        "TRANSCRIPT (downsampled: user turns + assistant text + tool names only):\n"
        f"{session.prompt_view}\n"
    )


def default_runner(prompt: str, *, model: str) -> dict:
    """Shell out to headless ``claude -p`` and return {"text","cost_usd"}.

    ``--bare`` skips hooks/plugins/skills/CLAUDE.md discovery: ~4x cheaper and
    ~2x faster per call, and this is a pure one-shot summarization that wants
    none of that context (auth is the ambient ``ANTHROPIC_API_KEY``).
    """
    env = {**os.environ, "CLAUDE_CONFIG_DIR": _ISOLATED_CONFIG_DIR}
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json",
         "--bare"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or proc.stdout or f"claude exited {proc.returncode}").strip()
        )
    envelope = json.loads(proc.stdout)
    return {
        "text": envelope.get("result", ""),
        "cost_usd": float(envelope.get("total_cost_usd") or 0.0),
    }


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    # if there is leading/trailing prose, grab the outermost {...}
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return text.strip()


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int, float))]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _coerce_artifacts(v) -> dict:
    v = v if isinstance(v, dict) else {}
    return {k: _as_list(v.get(k)) for k in ("branches", "prs", "files", "urls")}


def parse_reply(text: str) -> dict:
    """Parse a model reply into normalized record fields (lenient, never raises)."""
    try:
        data = json.loads(_strip_fence(text))
    except (json.JSONDecodeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    signals = [s for s in _as_list(data.get("status_signals")) if s in STATUS_SIGNALS]
    return {
        "title": (str(data.get("title") or "").strip() or "untitled session")[:200],
        "summary": str(data.get("summary") or "").strip(),
        "artifacts": _coerce_artifacts(data.get("artifacts")),
        "status_signals": signals or ["ongoing"],
        "candidate_slugs": _as_list(data.get("candidate_slugs")),
        "keywords": _as_list(data.get("keywords")),
    }


def _base_record(session: Session, *, now: datetime) -> dict:
    return {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "git_branch": session.git_branch,
        "t_start": session.t_start.isoformat() if session.t_start else None,
        "t_end": session.t_end.isoformat() if session.t_end else None,
        "n_messages": session.n_messages,
        "transcript_size": session.size,
        "trivial": session.trivial,
        "is_concierge": session.is_concierge,
        "hints": session.hints,
        "generated_at": now.isoformat(),
    }


def stub_record(session: Session, *, now: datetime | None = None) -> dict:
    """A trivial session's record — title from the first user message, no model."""
    now = now or datetime.now(timezone.utc)
    rec = _base_record(session, now=now)
    rec.update({
        "method": "stub",
        "model": None,
        "cost_usd": 0.0,
        "title": session.first_user or "trivial session",
        "summary": "",
        "artifacts": {"branches": [], "prs": [], "files": [], "urls": []},
        "status_signals": [],
        "candidate_slugs": [],
        "keywords": [],
    })
    return rec


def summarize_session(session: Session, registry_slugs: list[str], *,
                      runner=default_runner, model: str = config.MODEL,
                      now: datetime | None = None) -> dict:
    """Run one model call and return a full summary record."""
    now = now or datetime.now(timezone.utc)
    prompt = build_prompt(session, registry_slugs)
    result = runner(prompt, model=model)
    fields = parse_reply(result.get("text", ""))
    rec = _base_record(session, now=now)
    rec.update(fields)
    rec.update({
        "method": "model",
        "model": model,
        "cost_usd": float(result.get("cost_usd") or 0.0),
    })
    # a summarized session with an empty title falls back to its first user line
    if not rec["title"] or rec["title"] == "untitled session":
        rec["title"] = session.first_user or rec["title"]
    return rec
