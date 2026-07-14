"""Downloading and caching of arXiv artifacts (API metadata, HTML, PDF).

Raw downloads are cached under ``~/.cache/arxivist/<id-slug>/`` so repeated
outline/section reads of the same paper cost no network round-trips.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .ids import ArxivId

USER_AGENT = "arxivist/0.1 (https://github.com/dtch1997/arsenal; mailto:dtch009@gmail.com)"
_TIMEOUT = httpx.Timeout(30.0, read=120.0)


def cache_dir() -> Path:
    root = os.environ.get("ARXIVIST_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "arxivist"
    )
    return Path(root)


def paper_cache_dir(aid: ArxivId) -> Path:
    return cache_dir() / aid.slug


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT, follow_redirects=True
    )


def _cached_get(url: str, cache_path: Path, refresh: bool = False) -> bytes | None:
    """GET ``url``, caching the body at ``cache_path``. Returns None on 404.

    A 404 is also cached (as an empty sentinel file with suffix ``.404``) so we
    do not re-probe papers that have no HTML rendering on every call.
    """
    miss_marker = cache_path.with_suffix(cache_path.suffix + ".404")
    if not refresh:
        if cache_path.exists():
            return cache_path.read_bytes()
        if miss_marker.exists():
            return None
    with _client() as client:
        resp = client.get(url)
    if resp.status_code == 404:
        miss_marker.parent.mkdir(parents=True, exist_ok=True)
        miss_marker.touch()
        return None
    resp.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    miss_marker.unlink(missing_ok=True)
    return resp.content


def fetch_api_metadata(aid: ArxivId, refresh: bool = False) -> str:
    """Atom XML from the arXiv export API for this id."""
    url = f"https://export.arxiv.org/api/query?id_list={aid.versioned}&max_results=1"
    data = _cached_get(url, paper_cache_dir(aid) / "api.xml", refresh=refresh)
    if data is None:  # the API itself never 404s for well-formed queries
        raise RuntimeError(f"arXiv API returned 404 for {aid}")
    return data.decode("utf-8")


def fetch_html(aid: ArxivId, refresh: bool = False) -> str | None:
    """The paper's native LaTeXML HTML, or None if arXiv has no HTML for it."""
    url = f"https://arxiv.org/html/{aid.versioned}"
    data = _cached_get(url, paper_cache_dir(aid) / "paper.html", refresh=refresh)
    if data is None:
        return None
    text = data.decode("utf-8", errors="replace")
    # Papers without an HTML rendering sometimes 200 with an error page.
    if "No HTML for" in text[:4000]:
        return None
    return text


def fetch_pdf(aid: ArxivId, refresh: bool = False) -> Path:
    """Download the PDF into the cache and return its path."""
    path = paper_cache_dir(aid) / "paper.pdf"
    url = f"https://arxiv.org/pdf/{aid.versioned}"
    data = _cached_get(url, path, refresh=refresh)
    if data is None:
        raise RuntimeError(f"arXiv has no PDF for {aid} (404)")
    return path
