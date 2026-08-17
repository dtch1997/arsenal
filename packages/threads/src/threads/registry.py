"""The thread registry — ``~/jarvis-memory/MEMORY.md`` + its stub files.

There is exactly one registry (no second one): a slug is a stub filename stem.
``MEMORY.md`` supplies each slug's one-line status text, which the dashboard
reads to decide whether a dormant thread should be *flagged* (a line that
already reads closed/retired/complete is not flagged).

This module never writes into ``~/jarvis-memory`` — promotion of candidate
threads is a human/consolidation job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config

# a MEMORY.md status line whose text reads as "the whole thread is finished"
# suppresses the dormancy flag. Deliberately excludes partial-progress words
# like "merged"/"done"/"shipped" that routinely appear in still-active lines
# ("PR #97 MERGED, next = ...").
CLOSED_WORDS = (
    "closed", "retired", "complete", "completed", "wrapped", "abandoned",
    "archived",
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


@dataclass
class Registry:
    slugs: set[str]
    lines: dict[str, str]            # slug -> MEMORY.md status text (may be "")
    stub_text: dict[str, str]        # slug -> lowercased stub body
    _repo_cache: dict[str, str | None] = field(default_factory=dict)

    def has(self, slug: str) -> bool:
        return slug in self.slugs

    def is_closed(self, slug: str) -> bool:
        line = self.lines.get(slug, "").lower()
        return any(w in line for w in CLOSED_WORDS)

    def repo_to_slug(self, repo: str) -> str | None:
        """Map a repo/dir name to a slug: exact, then stub mention, then substring."""
        if repo in self._repo_cache:
            return self._repo_cache[repo]
        result = self._repo_to_slug(repo)
        self._repo_cache[repo] = result
        return result

    def _repo_to_slug(self, repo: str) -> str | None:
        n = _norm(repo)
        if not n:
            return None
        if n in self.slugs:
            return n
        needle = re.compile(rf"\b{re.escape(repo.lower())}\b")
        for slug in sorted(self.slugs):
            body = self.stub_text.get(slug, "")
            if f"repos/{repo.lower()}" in body or needle.search(body):
                return slug
        for slug in sorted(self.slugs):
            if n in slug or slug in n:
                return slug
        return None

    def branch_to_slug(self, branch: str) -> str | None:
        nb = _norm(branch)
        if not nb:
            return None
        for slug in sorted(self.slugs):
            if nb == slug:
                return slug
        for slug in sorted(self.slugs):
            if nb in slug or slug in nb:
                return slug
        return None


_LINE = re.compile(r"^\s*[-*]\s*\[[^\]]+\]\(([A-Za-z0-9_.-]+)\.md\)\s*(.*)$")


def _parse_memory_index(text: str) -> dict[str, str]:
    """slug -> the remainder of its ``- [title](slug.md) — …`` line."""
    lines = {}
    for row in text.splitlines():
        m = _LINE.match(row)
        if m:
            slug, rest = m.group(1), m.group(2)
            lines[slug] = rest.lstrip("— -").strip()
    return lines


def load_registry() -> Registry:
    mem = config.memory_dir()
    slugs: set[str] = set()
    stub_text: dict[str, str] = {}
    if mem.is_dir():
        for p in sorted(mem.glob("*.md")):
            if p.name == "MEMORY.md":
                continue
            slug = p.stem
            slugs.add(slug)
            try:
                stub_text[slug] = p.read_text(errors="replace").lower()
            except OSError:
                stub_text[slug] = ""
    lines: dict[str, str] = {}
    index = mem / "MEMORY.md"
    if index.exists():
        try:
            lines = _parse_memory_index(index.read_text(errors="replace"))
        except OSError:
            lines = {}
    return Registry(slugs=slugs, lines=lines, stub_text=stub_text)


def memory_index_slugs() -> list[str]:
    """Slug + one-line description pairs for the summarizer prompt (from MEMORY.md
    where a line exists, else the bare slug)."""
    reg = load_registry()
    out = []
    for slug in sorted(reg.slugs):
        line = reg.lines.get(slug, "")
        out.append(f"{slug}: {line}" if line else slug)
    return out
