"""arxivist — arXiv papers as structured, agent-legible markdown.

    import arxivist
    paper = arxivist.get("2401.12345")        # id, id+version, or any arxiv URL
    print(paper.outline())                     # TOC with word counts
    print(paper.get_section("3.2").content)    # read just what you need
    paper.save("papers/2401.12345")            # paper.md + paper.json

Content comes from arXiv's native HTML rendering when available (clean
structure, real LaTeX math), falling back to PDF extraction otherwise.
Everything is cached under ``~/.cache/arxivist/``.
"""

from __future__ import annotations

import json

from . import fetch as _fetch
from .ids import ArxivId, InvalidArxivId, parse_arxiv_id
from .metadata import PaperMetadata, parse_api_metadata
from .models import Author, Figure, Paper, Reference, Section

__all__ = [
    "get",
    "get_metadata",
    "Paper",
    "Section",
    "Author",
    "Reference",
    "Figure",
    "PaperMetadata",
    "ArxivId",
    "InvalidArxivId",
    "parse_arxiv_id",
]

__version__ = "0.1.0"


def get_metadata(id_or_url: str, refresh: bool = False) -> PaperMetadata:
    """Fetch just the paper's metadata (title/abstract/authors/...) from the API."""
    aid = parse_arxiv_id(id_or_url)
    return parse_api_metadata(_fetch.fetch_api_metadata(aid, refresh=refresh))


def get(id_or_url: str, refresh: bool = False, prefer: str | None = None) -> Paper:
    """Download and parse a paper into a :class:`Paper`.

    Tries arXiv's native HTML first, falls back to PDF extraction. Both raw
    downloads and the parsed result are cached; ``refresh=True`` re-downloads.
    ``prefer="pdf"`` forces the PDF path (e.g. to compare parser outputs).
    """
    aid = parse_arxiv_id(id_or_url)
    parsed_cache = _fetch.paper_cache_dir(aid) / ("parsed.json" if prefer is None else f"parsed-{prefer}.json")
    if not refresh and parsed_cache.exists():
        return Paper.from_dict(json.loads(parsed_cache.read_text(encoding="utf-8")))

    meta = parse_api_metadata(_fetch.fetch_api_metadata(aid, refresh=refresh))
    paper: Paper | None = None
    if prefer != "pdf":
        html = _fetch.fetch_html(aid, refresh=refresh)
        if html is not None:
            from .html_parser import parse_html

            paper = parse_html(html, meta)
            if not paper.sections:  # HTML present but structurally empty
                paper = None
    if paper is None:
        from .pdf_parser import parse_pdf

        paper = parse_pdf(_fetch.fetch_pdf(aid, refresh=refresh), meta)

    parsed_cache.parent.mkdir(parents=True, exist_ok=True)
    parsed_cache.write_text(
        json.dumps(paper.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return paper
