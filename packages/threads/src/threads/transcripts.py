"""Discover and parse ``~/.claude/projects/**/*.jsonl`` transcripts.

Each ``.jsonl`` file is one session. We read it defensively — a malformed or
truncated line is skipped and tallied, never fatal — and produce a
:class:`Session`: the metadata a summary record needs (ids, cwd, branch, time
span, message count, size) plus two derived products:

* a **downsampled prompt view** (real user turns + assistant text + tool
  *names*; tool inputs/outputs elided; head+tail hard-capped) fed to the
  summarizer, and
* deterministic **match hints** (memory-stub edits, goals edits, touched-repo
  frequencies, branch names, PR references) that :mod:`threads.weave` matches
  against the registry with zero model calls.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config

# hard cap on the prompt-view size handed to the summarizer (head+tail).
PROMPT_CAP = 50_000

TRIVIAL_MIN_MESSAGES = 10
TRIVIAL_MIN_SPAN_MIN = 5.0

_HOME = str(Path.home())

# owner/repo references in transcript text: github URLs and the two known
# owners. Group 1 is the repo name.
_REPO_REF = re.compile(
    r"(?:github\.com/[A-Za-z0-9_.-]+/|ArcadiaImpact/|arcadiaimpact/|dtch1997/)"
    r"([A-Za-z0-9_.-]+)"
)
# `owner/repo#123` PR references anywhere in text.
_PR_REF = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b")


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@dataclass
class Session:
    session_id: str
    path: Path
    size: int
    cwd: str | None
    git_branch: str | None
    t_start: datetime | None
    t_end: datetime | None
    n_messages: int
    first_user: str
    prompt_view: str
    hints: dict
    parse_warnings: int = 0

    @property
    def span_minutes(self) -> float:
        if self.t_start and self.t_end:
            return max(0.0, (self.t_end - self.t_start).total_seconds() / 60.0)
        return 0.0

    @property
    def trivial(self) -> bool:
        return (
            self.n_messages < TRIVIAL_MIN_MESSAGES
            or self.span_minutes < TRIVIAL_MIN_SPAN_MIN
        )

    @property
    def is_concierge(self) -> bool:
        return bool(self.cwd and "concierge-home/workspaces" in self.cwd)


def _text_of(content) -> list[str]:
    """Real text blocks of a message (user string / assistant text), no tools."""
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text")
                if isinstance(t, str):
                    out.append(t)
    return out


def _is_tool_result_only(content) -> bool:
    """A user message that only carries tool_result blocks is not a real turn."""
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _extract_paths(content) -> list[str]:
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp = b.get("input") or {}
                if isinstance(inp, dict):
                    for k in ("file_path", "path", "notebook_path"):
                        v = inp.get(k)
                        if isinstance(v, str):
                            out.append(v)
    return out


def _tool_names(content) -> list[str]:
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                name = b.get("name")
                if isinstance(name, str):
                    out.append(name)
    return out


def _build_hints(paths: list[str], cwd: str | None, branch: str | None,
                 text: str) -> dict:
    stubs, goals = [], []
    repos: Counter = Counter()
    for p in paths:
        rel = p.replace(_HOME, "~")
        base = os.path.basename(p)
        if rel.startswith("~/jarvis-memory/") and base.endswith(".md") \
                and base != "MEMORY.md":
            stubs.append(base[:-3])
        m = re.search(r"/goals/([A-Za-z0-9_-]+)\.md", rel)
        if m:
            goals.append(m.group(1))
        m = re.search(r"/repos/([^/]+)", rel) or re.search(r"~/(phd-thesis)\b", rel)
        if m:
            repos[m.group(1)] += 1
    if cwd:
        m = re.search(r"/repos/([^/]+)", cwd) or re.search(r"/(phd-thesis)/?$", cwd)
        if m:
            repos[m.group(1)] += 3  # the working dir is a strong signal
    for m in _REPO_REF.finditer(text):
        repos[m.group(1)] += 1
    prs = sorted({f"{o}#{n}" for o, n in _PR_REF.findall(text)})
    branches = []
    if branch and branch not in ("main", "HEAD"):
        branches.append(branch)
    return {
        "stubs": sorted(set(stubs)),
        "goals": sorted(set(goals)),
        "repos": [r for r, _ in repos.most_common()],
        "branches": branches,
        "prs": prs,
    }


def _downsample(turns: list[tuple[str, str]]) -> str:
    """Join labelled turns, head+tail capped at :data:`PROMPT_CAP` chars."""
    full = "\n\n".join(f"[{role}] {body}" for role, body in turns if body.strip())
    if len(full) <= PROMPT_CAP:
        return full
    head = full[: PROMPT_CAP // 2]
    tail = full[-PROMPT_CAP // 2:]
    return head + "\n\n...[transcript elided]...\n\n" + tail


def parse_transcript(path: Path) -> Session:
    """Parse one ``.jsonl`` transcript into a :class:`Session` (never raises)."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    # the filename stem is the stable spool identity (it is unique per file and
    # stat-able without parsing, which the idempotency check relies on).
    session_id = path.stem
    cwd = branch = None
    n_messages = 0
    warnings = 0
    times: list[datetime] = []
    first_user = ""
    paths: list[str] = []
    text_parts: list[str] = []
    turns: list[tuple[str, str]] = []

    try:
        raw_lines = path.read_text(errors="replace").splitlines()
    except OSError:
        raw_lines = []

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            warnings += 1
            continue
        if not isinstance(d, dict):
            warnings += 1
            continue
        if d.get("cwd") and not cwd:
            cwd = d["cwd"]
        if d.get("gitBranch") and not branch:
            branch = d["gitBranch"]
        typ = d.get("type")
        if typ not in ("user", "assistant"):
            continue
        n_messages += 1
        ts = _parse_ts(d.get("timestamp"))
        if ts:
            times.append(ts)
        msg = d.get("message") or {}
        content = msg.get("content")
        paths.extend(_extract_paths(content))
        texts = _text_of(content)
        text_parts.extend(texts)
        if typ == "user" and not _is_tool_result_only(content):
            body = " ".join(texts).strip()
            if body:
                if not first_user:
                    first_user = body
                turns.append(("user", body))
        elif typ == "assistant":
            body = " ".join(texts).strip()
            tools = _tool_names(content)
            if tools:
                body = (body + " " if body else "") + "{tools: " + ", ".join(tools) + "}"
            if body.strip():
                turns.append(("assistant", body))

    times.sort()
    t_start = times[0] if times else None
    t_end = times[-1] if times else None
    joined_text = " ".join(text_parts)
    hints = _build_hints(paths, cwd, branch, joined_text[:PROMPT_CAP])
    return Session(
        session_id=session_id,
        path=path,
        size=size,
        cwd=cwd,
        git_branch=branch,
        t_start=t_start,
        t_end=t_end,
        n_messages=n_messages,
        first_user=_clean_title(first_user),
        prompt_view=_downsample(turns),
        hints=hints,
        parse_warnings=warnings,
    )


_CAVEAT = re.compile(r"<[^>]+>")


def _clean_title(text: str) -> str:
    """First user line, stripped of command-caveat noise, one line, trimmed."""
    text = _CAVEAT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


# The summarizer/cluster subprocesses are `claude -p` sessions whose first user
# message *is* our prompt; even with CLAUDE_CONFIG_DIR isolation, any that landed
# in the corpus (e.g. from an earlier build) must not be scanned — else scanning
# reflects on itself. These preambles are the reliable discriminator.
SELF_MARKERS = (
    "You are summarizing a Claude Code session transcript",
    "You are grouping unfiled Claude sessions into candidate threads",
)


def is_self_reflection(path: Path) -> bool:
    """True if this transcript is one of threads' own summarizer/cluster calls."""
    try:
        with path.open(errors="replace") as f:
            for _ in range(5):  # the prompt is the first user turn
                line = f.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "user":
                    continue
                content = (d.get("message") or {}).get("content")
                text = content if isinstance(content, str) else " ".join(
                    _text_of(content))
                return any(text.lstrip().startswith(m) for m in SELF_MARKERS)
    except OSError:
        return False
    return False


def iter_transcript_paths(days: int, *, now: datetime | None = None) -> list[Path]:
    """Transcript files whose mtime is within the last ``days``, excluding
    threads' own summarizer/cluster reflections."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - days * 86400
    root = config.projects_dir()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("**/*.jsonl")):
        try:
            if p.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        if is_self_reflection(p):
            continue
        out.append(p)
    return out
