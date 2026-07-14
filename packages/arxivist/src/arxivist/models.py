"""Structured representation of a parsed paper.

The design goal is agent legibility: a ``Paper`` renders to clean markdown
with real LaTeX math, exposes a numbered outline with word counts (so an
agent can budget its reading), and supports pulling a single section by
number or title instead of the whole document.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Author:
    name: str
    affiliation: str | None = None


@dataclass
class Reference:
    key: str  # anchor id, e.g. "bib.bib12", or "ref-12" for PDF-derived refs
    label: str  # in-text label, e.g. "12" or "Smith et al. (2020)"
    text: str  # full formatted reference string


@dataclass
class Figure:
    label: str | None  # e.g. "Figure 3"
    caption: str


@dataclass
class Section:
    title: str
    number: str | None = None  # e.g. "3.2"; None for unnumbered sections
    content: str = ""  # markdown body of this section, excluding children
    children: list["Section"] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def word_count(self) -> int:
        return len(self.content.split()) + sum(c.word_count for c in self.children)

    def to_markdown(self, depth: int = 1) -> str:
        heading = "#" * min(depth + 1, 6)
        label = f"{self.number} {self.title}" if self.number else self.title
        parts = [f"{heading} {label}"]
        if self.content.strip():
            parts.append(self.content.strip())
        parts.extend(c.to_markdown(depth + 1) for c in self.children)
        return "\n\n".join(parts)


@dataclass
class Paper:
    arxiv_id: str
    version: int | None
    title: str
    authors: list[Author]
    abstract: str
    categories: list[str] = field(default_factory=list)
    published: str | None = None  # ISO date of v1
    updated: str | None = None  # ISO date of the fetched version
    comment: str | None = None  # author comment (venue, page count, ...)
    doi: str | None = None
    source: str = "html"  # which parser produced the body: "html" | "pdf"
    sections: list[Section] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)

    # ------------------------------------------------------------------ views

    def all_sections(self):
        for s in self.sections:
            yield from s.walk()

    def outline(self) -> str:
        """Numbered table of contents with word counts, for read-budgeting."""
        lines = [f"{self.title}", f"({self.arxiv_id}, parsed from {self.source}, "
                 f"{sum(s.word_count for s in self.sections)} words)", ""]

        def add(section: Section, depth: int) -> None:
            label = f"{section.number} {section.title}" if section.number else section.title
            lines.append(f"{'  ' * depth}- {label} [{section.word_count} words]")
            for child in section.children:
                add(child, depth + 1)

        for s in self.sections:
            add(s, 0)
        if self.references:
            lines.append(f"- References [{len(self.references)} entries]")
        return "\n".join(lines)

    def get_section(self, query: str) -> Section | None:
        """Find a section by number ("3.2") or case-insensitive title substring."""
        q = query.strip().rstrip(".")
        for s in self.all_sections():
            if s.number is not None and s.number.rstrip(".") == q:
                return s
        ql = q.lower()
        for s in self.all_sections():
            if s.title.lower() == ql:
                return s
        for s in self.all_sections():
            if ql in s.title.lower():
                return s
        return None

    def to_markdown(self, include_references: bool = True) -> str:
        head = [f"# {self.title}", ""]
        if self.authors:
            head.append("**Authors:** " + ", ".join(a.name for a in self.authors))
        meta_bits = [f"arXiv:{self.arxiv_id}" + (f"v{self.version}" if self.version else "")]
        if self.published:
            meta_bits.append(f"published {self.published[:10]}")
        if self.categories:
            meta_bits.append(", ".join(self.categories))
        head.append("**Meta:** " + " | ".join(meta_bits))
        if self.comment:
            head.append(f"**Comment:** {self.comment}")
        head += ["", "## Abstract", "", self.abstract.strip()]
        body = [s.to_markdown(depth=1) for s in self.sections]
        parts = ["\n".join(head)] + body
        if include_references and self.references:
            refs = ["## References", ""]
            refs += [f"- [{r.label}] {r.text}" for r in self.references]
            parts.append("\n".join(refs))
        return "\n\n".join(parts) + "\n"

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        def mk_section(sd: dict) -> Section:
            children = [mk_section(c) for c in sd.pop("children", [])]
            return Section(**{**sd, "children": children})

        d = dict(d)
        d["authors"] = [Author(**a) for a in d.get("authors", [])]
        d["sections"] = [mk_section(s) for s in d.get("sections", [])]
        d["references"] = [Reference(**r) for r in d.get("references", [])]
        d["figures"] = [Figure(**f) for f in d.get("figures", [])]
        return cls(**d)

    def save(self, directory: str | Path) -> Path:
        """Write ``paper.md`` + ``paper.json`` into ``directory``; returns it."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "paper.md").write_text(self.to_markdown(), encoding="utf-8")
        (directory / "paper.json").write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return directory

    @classmethod
    def load(cls, directory: str | Path) -> "Paper":
        data = json.loads((Path(directory) / "paper.json").read_text(encoding="utf-8"))
        return cls.from_dict(data)
