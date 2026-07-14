"""Live network tests — hit arxiv.org for real. Skipped unless RUN_LIVE=1.

    RUN_LIVE=1 pytest packages/arxivist/tests/test_live.py -v
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1", reason="live network test; set RUN_LIVE=1"
)


def test_html_path_recent_paper(tmp_path, monkeypatch):
    monkeypatch.setenv("ARXIVIST_CACHE", str(tmp_path))
    import arxivist

    # "Sleeper Agents" (Jan 2024) — has a native HTML rendering
    paper = arxivist.get("2401.05566")
    assert paper.source == "html"
    assert "sleeper" in paper.title.lower()
    assert len(paper.sections) > 3
    assert len(paper.references) > 10
    assert paper.get_section("Introduction") is not None
    assert "$" in paper.to_markdown()  # math survived as LaTeX


def test_pdf_path_forced(tmp_path, monkeypatch):
    monkeypatch.setenv("ARXIVIST_CACHE", str(tmp_path))
    import arxivist

    # arXiv has back-rendered HTML for most old papers now, so the natural
    # fallback rarely triggers; force the PDF parser to exercise that path.
    paper = arxivist.get("1706.03762", prefer="pdf")
    assert paper.source == "pdf"
    assert "attention" in paper.title.lower()
    assert paper.get_section("Introduction") is not None


def test_metadata_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ARXIVIST_CACHE", str(tmp_path))
    import arxivist

    meta = arxivist.get_metadata("https://arxiv.org/abs/1706.03762")
    assert meta.title == "Attention Is All You Need"
    assert any("Vaswani" in a.name for a in meta.authors)
