"""PDF fallback: pymupdf4llm markdown → best-effort section tree.

Used for papers arXiv has no HTML rendering for (mostly pre-2024 submissions).
Quality is inherently below the HTML path — math becomes glyph soup, and the
section tree is reconstructed from font-size-derived markdown headings — but
metadata (title/abstract/authors) still comes clean from the arXiv API.
"""

from __future__ import annotations

import re
from pathlib import Path

from .metadata import PaperMetadata
from .models import Paper, Reference, Section

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# "3.2 Method" / "3.2. Method" / "A.1 Proofs" — leading section number
_NUMBERED_TITLE_RE = re.compile(r"^(?P<num>(?:[A-Z]|\d+)(?:\.\d+)*)\.?\s+(?P<title>\S.*)$")
_REF_SPLIT_RE = re.compile(r"\n(?=\[\d+\]\s)")


def _split_heading(raw_title: str) -> tuple[str | None, str]:
    title = raw_title.strip().strip("*_ ").strip()
    m = _NUMBERED_TITLE_RE.match(title)
    if m and (m.group("num")[0].isdigit() or "." in m.group("num")):
        return m.group("num"), m.group("title")
    return None, title


def _extract_references(sections: list[Section]) -> list[Reference]:
    """Pop a References/Bibliography section and split it into entries."""
    for parent_list in [sections] + [s.children for sec in sections for s in sec.walk()]:
        for i, s in enumerate(parent_list):
            if s.title.strip().lower() in ("references", "bibliography") and not s.children:
                parent_list.pop(i)
                chunks = _REF_SPLIT_RE.split(s.content)
                if len(chunks) <= 1:  # unnumbered styles: split on blank lines
                    chunks = [c for c in s.content.split("\n\n") if c.strip()]
                refs = []
                for j, chunk in enumerate(chunks, start=1):
                    text = re.sub(r"\s+", " ", chunk).strip()
                    if not text:
                        continue
                    m = re.match(r"^\[(\d+)\]\s*(.*)$", text)
                    label, body = (m.group(1), m.group(2)) if m else (str(j), text)
                    refs.append(Reference(key=f"ref-{label}", label=label, text=body))
                return refs
    return []


def parse_pdf_markdown(md: str, meta: PaperMetadata) -> Paper:
    """Build a Paper from pymupdf4llm markdown output."""
    # Section tree from heading lines. Everything before the first heading is
    # PDF front matter (title/authors/abstract) that the API already provides
    # more cleanly — keep it, but under an explicit label.
    root = Section(title="__root__")
    stack: list[tuple[int, Section]] = [(0, root)]
    buffer: list[str] = []
    in_code = False

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        node = stack[-1][1]
        node.content = f"{node.content}\n\n{text}".strip()

    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING_RE.match(line)
        if not m:
            buffer.append(line)
            continue
        flush()
        level = len(m.group(1))
        number, title = _split_heading(m.group(2))
        if number:
            # font-size-derived levels are often flat (3.2 and 3.2.1 both
            # "##"); the section number encodes the real nesting depth
            level = number.count(".") + 2
        while stack and stack[-1][0] >= level:
            stack.pop()
        section = Section(title=title, number=number)
        (stack[-1][1] if stack else root).children.append(section)
        stack.append((level, section))
    flush()

    sections = root.children
    if root.content:
        sections.insert(0, Section(title="(front matter)", content=root.content))
    references = _extract_references(sections)

    return Paper(
        arxiv_id=meta.arxiv_id,
        version=meta.version,
        title=meta.title,
        authors=meta.authors,
        abstract=meta.abstract,
        categories=meta.categories,
        published=meta.published,
        updated=meta.updated,
        comment=meta.comment,
        doi=meta.doi,
        source="pdf",
        sections=sections,
        references=references,
    )


def parse_pdf(pdf_path: str | Path, meta: PaperMetadata) -> Paper:
    import pymupdf4llm  # deferred: heavy import

    md = pymupdf4llm.to_markdown(str(pdf_path), show_progress=False)
    return parse_pdf_markdown(md, meta)
