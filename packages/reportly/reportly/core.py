"""Parse an experiment report into a structured model the linter and builder share.

A report is a Markdown file with an optional leading ``---`` front-matter block.
We extract the pieces the standard cares about: the front-matter, the H1 thesis,
the *anchors* (headings **and** bold-lead spans like ``**TL;DR.**`` — both count
as section markers, since the convention uses either), embedded figures, fenced
code blocks, and a one-line summary for the index.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A leading ``---`` ... ``---`` block of simple ``key: value`` lines (YAML-ish).
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.M)
# A paragraph that opens with a bold span, e.g. ``**TL;DR.** ...`` — treated as a
# section anchor so reports that lead with bold instead of a heading still match.
_BOLD_LEAD = re.compile(r"^\s*\*\*(.+?)\*\*", re.M)
_IMAGE = re.compile(r"!\[(?P<alt>.*?)\]\((?P<src>.*?)\)")
_FENCE = re.compile(r"^```(?P<lang>[^\n]*)\n(?P<code>.*?)\n```", re.M | re.S)


@dataclass
class Anchor:
    """A section marker: a heading (``level`` 1-6) or a bold-lead (``level`` 0)."""

    text: str
    level: int
    line: int

    @property
    def norm(self) -> str:
        return self.text.strip().lower()


@dataclass
class Figure:
    alt: str
    src: str
    line: int

    @property
    def is_local(self) -> bool:
        return not re.match(r"^[a-z]+://", self.src) and not self.src.startswith("data:")


@dataclass
class Report:
    path: Path
    raw: str
    meta: dict[str, str]
    body: str  # text after front matter
    anchors: list[Anchor] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    code_blocks: list[tuple[str, str, int]] = field(default_factory=list)  # (lang, code, line)

    @property
    def title(self) -> str | None:
        return first_title(self.body)

    @property
    def summary(self) -> str:
        return summary(self.body)

    def headings(self, level: int | None = None) -> list[Anchor]:
        return [a for a in self.anchors if a.level >= 1 and (level is None or a.level == level)]

    def section_of(self, line: int) -> Anchor | None:
        """The nearest heading at or above ``line`` — i.e. which section a line is in."""
        cur = None
        for a in self.anchors:
            if a.level >= 1 and a.line <= line:
                cur = a
        return cur


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading ``---`` block off the body. ``meta`` is empty if absent."""
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            # strip an inline ``# comment`` and surrounding quotes
            v = v.split(" #", 1)[0].strip().strip("'\"")
            meta[k.strip().lower()] = v
    return meta, text[m.end():]


def first_title(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*#*$", text, re.M)
    return m.group(1).strip() if m else None


def summary(text: str) -> str:
    """First real paragraph after the H1, stripped of markdown emphasis/links."""
    body = re.sub(r"^#\s+.+$", "", text, count=1, flags=re.M).strip()
    para = next((b.strip() for b in body.split("\n\n") if b.strip()), "")
    para = re.sub(r"!\[.*?\]\(.*?\)", "", para)
    para = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", para)
    para = re.sub(r"[*`_#]+", "", para).replace("\n", " ")
    para = re.sub(r"\s+", " ", para).strip()
    return (para[:300] + "…") if len(para) > 300 else para


def _strip_code(body: str) -> str:
    """Blank out fenced code so headings/figures inside code aren't mistaken for real."""
    return _FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), body)


def parse(path: str | Path, text: str | None = None) -> Report:
    path = Path(path)
    raw = text if text is not None else path.read_text()
    meta, body = parse_front_matter(raw)

    code_blocks = [
        (m.group("lang").strip(), m.group("code"), _line_of(body, m.start()))
        for m in _FENCE.finditer(body)
    ]
    scan = _strip_code(body)  # ignore markup that lives inside code fences

    anchors: list[Anchor] = []
    for m in _HEADING.finditer(scan):
        anchors.append(Anchor(m.group(2).strip(), len(m.group(1)), _line_of(scan, m.start())))
    for m in _BOLD_LEAD.finditer(scan):
        anchors.append(Anchor(m.group(1).strip().rstrip(".:"), 0, _line_of(scan, m.start())))
    anchors.sort(key=lambda a: a.line)

    figures = [
        Figure(m.group("alt"), m.group("src"), _line_of(scan, m.start()))
        for m in _IMAGE.finditer(scan)
    ]
    return Report(path=path, raw=raw, meta=meta, body=body,
                  anchors=anchors, figures=figures, code_blocks=code_blocks)
