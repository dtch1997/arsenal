"""Fixtures: a fake ``~/.claude/projects`` tree, a fake registry, fake concierge
tasks, and a canned summarizer runner. No network, no real ``claude``, no real
home dirs — every path is redirected under ``tmp_path`` via env overrides.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    projects = tmp_path / "claude" / "projects"
    memory = tmp_path / "jarvis-memory"
    concierge = tmp_path / "concierge-home"
    goals = tmp_path / "goals"
    for d in (projects, memory, concierge / "tasks", concierge / "specs", goals):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("THREADS_HOME", str(tmp_path / "threadshome"))
    monkeypatch.setenv("THREADS_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("THREADS_MEMORY_DIR", str(memory))
    monkeypatch.setenv("THREADS_CONCIERGE_HOME", str(concierge))
    monkeypatch.setenv("THREADS_GOALS_DIR", str(goals))
    monkeypatch.setenv("FLARE_HOME", str(tmp_path / "flarehome"))
    monkeypatch.delenv("FLARE_WEBHOOK", raising=False)
    return Env(tmp_path, projects, memory, concierge, goals)


class Env:
    def __init__(self, root, projects, memory, concierge, goals):
        self.root = root
        self.projects = projects
        self.memory = memory
        self.concierge = concierge
        self.goals = goals

    # ----- goals + hierarchy -----
    def add_goal(self, slug: str, *, title: str = "", body: str = "",
                 mentions=()):
        text = f"---\nslug: {slug}\n---\n\n# {title or slug}\n\n{body}\n"
        for m in mentions:
            text += f"\nWorking on {m} this cycle.\n"
        (self.goals / f"{slug}.md").write_text(text)

    def write_hierarchy(self, text: str):
        from threads import config
        config.ensure_spool()
        config.hierarchy_path().write_text(text)

    # ----- registry -----
    def add_stub(self, slug: str, body: str = "", memory_line: str | None = None):
        (self.memory / f"{slug}.md").write_text(body or f"# {slug}\n")
        if memory_line is not None:
            index = self.memory / "MEMORY.md"
            prev = index.read_text() if index.exists() else "# Memory index\n"
            index.write_text(prev + f"- [{slug}]({slug}.md) — {memory_line}\n")

    # ----- concierge -----
    def add_task(self, tid: str, title: str = "", repo=None, spec_text: str = ""):
        task = {"id": tid, "title": title,
                "workspace": {"repo": repo, "branch": f"pool/{tid}"}}
        if spec_text:
            task["spec"] = f"specs/{tid}.md"
            (self.concierge / "specs" / f"{tid}.md").write_text(spec_text)
        (self.concierge / "tasks" / f"{tid}.json").write_text(json.dumps(task))

    # ----- transcripts -----
    def add_transcript(self, project: str, session_id: str, *,
                       cwd: str, branch: str = "main",
                       n_pairs: int = 8, span_min: float = 30.0,
                       tool_paths=None, extra_text: str = "",
                       malformed: bool = False, start=None,
                       mtime: datetime | None = None) -> "os.PathLike":
        start = start or (NOW - timedelta(hours=2))
        pdir = self.projects / project
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / f"{session_id}.jsonl"
        lines = []
        step = (span_min / max(1, n_pairs)) if n_pairs else 0
        for i in range(n_pairs):
            ts = (start + timedelta(minutes=step * i)).isoformat()
            lines.append(json.dumps({
                "type": "user", "sessionId": session_id, "cwd": cwd,
                "gitBranch": branch, "timestamp": ts,
                "message": {"role": "user", "content":
                            f"user turn {i} {extra_text if i == 0 else ''}"},
            }))
            content = [{"type": "text", "text": f"assistant reply {i}"}]
            if tool_paths and i == 0:
                for p in tool_paths:
                    content.append({"type": "tool_use", "name": "Edit",
                                    "id": f"t{i}", "input": {"file_path": p}})
            lines.append(json.dumps({
                "type": "assistant", "sessionId": session_id, "cwd": cwd,
                "gitBranch": branch, "timestamp": ts,
                "message": {"role": "assistant", "content": content},
            }))
        if malformed:
            lines.insert(1, "{ this is not valid json ")
            lines.append('{"type":"assistant","message":{truncated')
        path.write_text("\n".join(lines) + "\n")
        m = (mtime or NOW).timestamp()
        os.utime(path, (m, m))
        return path


@pytest.fixture
def canned_runner():
    """A summarizer runner that returns a valid record and counts calls."""
    calls = {"n": 0, "prompts": []}

    def runner(prompt: str, *, model: str) -> dict:
        calls["n"] += 1
        calls["prompts"].append(prompt)
        rec = {
            "title": "did a thing",
            "summary": "Attempted X. It worked. Wrapped up.",
            "artifacts": {"branches": [], "prs": [], "files": [], "urls": []},
            "status_signals": ["wrapped-up"],
            "candidate_slugs": [],
            "keywords": ["x", "y"],
        }
        return {"text": json.dumps(rec), "cost_usd": 0.002}

    runner.calls = calls
    return runner


@pytest.fixture
def write_record():
    """Write a summary record straight into the spool (for weave tests)."""
    from threads import spool

    def _w(session_id, *, cwd="/home/x/jarvis", trivial=False,
           is_concierge=False, hints=None, candidate_slugs=None,
           t_end=None, title="t", method="model", keywords=None):
        rec = {
            "session_id": session_id, "cwd": cwd, "git_branch": "main",
            "t_start": (NOW - timedelta(hours=1)).isoformat(),
            "t_end": (t_end or NOW).isoformat(),
            "n_messages": 20, "transcript_size": 100, "trivial": trivial,
            "is_concierge": is_concierge,
            "hints": hints or {"stubs": [], "goals": [], "repos": [],
                               "branches": [], "prs": []},
            "method": method, "model": "m", "cost_usd": 0.0,
            "title": title, "summary": "s",
            "artifacts": {"branches": [], "prs": [], "files": [], "urls": []},
            "status_signals": ["ongoing"],
            "candidate_slugs": candidate_slugs or [], "keywords": keywords or [],
            "generated_at": NOW.isoformat(),
        }
        spool.write_summary(rec)
        return rec

    return _w
