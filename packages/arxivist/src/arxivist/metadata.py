"""Parse arXiv export-API Atom XML into paper metadata (stdlib only)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .models import Author

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass
class PaperMetadata:
    arxiv_id: str
    version: int | None
    title: str
    abstract: str
    authors: list[Author] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    published: str | None = None
    updated: str | None = None
    comment: str | None = None
    doi: str | None = None


def _clean(text: str | None) -> str:
    """The API hard-wraps text; collapse whitespace back to single spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def parse_api_metadata(atom_xml: str) -> PaperMetadata:
    root = ET.fromstring(atom_xml)
    entry = root.find("atom:entry", _NS)
    if entry is None:
        raise ValueError("arXiv API response contains no entry")
    id_url = entry.findtext("atom:id", default="", namespaces=_NS)
    # e.g. http://arxiv.org/abs/2401.12345v2
    m = re.search(r"abs/(?P<id>.+?)(?:v(?P<v>\d+))?$", id_url)
    if not m:
        raise ValueError(f"unexpected id in API response: {id_url!r}")
    title = _clean(entry.findtext("atom:title", namespaces=_NS))
    if title.lower() == "error":  # the API reports bad ids as an "Error" entry
        summary = _clean(entry.findtext("atom:summary", namespaces=_NS))
        raise ValueError(f"arXiv API error: {summary}")
    return PaperMetadata(
        arxiv_id=m.group("id"),
        version=int(m.group("v")) if m.group("v") else None,
        title=title,
        abstract=_clean(entry.findtext("atom:summary", namespaces=_NS)),
        authors=[
            Author(name=_clean(a.findtext("atom:name", namespaces=_NS)))
            for a in entry.findall("atom:author", _NS)
        ],
        categories=[
            c.get("term", "") for c in entry.findall("atom:category", _NS) if c.get("term")
        ],
        published=entry.findtext("atom:published", namespaces=_NS),
        updated=entry.findtext("atom:updated", namespaces=_NS),
        comment=_clean(entry.findtext("arxiv:comment", namespaces=_NS)) or None,
        doi=_clean(entry.findtext("arxiv:doi", namespaces=_NS)) or None,
    )
