"""Regression tests for the emitted editor page.

The page template is a plain (non-raw) Python string, so an unescaped `\\n`
in its inline JavaScript becomes a real newline inside a JS string literal —
a SyntaxError that kills the whole editor script (dead Save button, dead
Cmd+S). v0.1.x shipped exactly that; parse the emitted JS to keep it fixed.
"""

import re
import shutil
import subprocess

import pytest

from cowrite.render import analyze_comments, build_page, render_fragment, strip_comment_markers

NASTY_MD = "# T\n\n$a+b$ and `code` and __PREVIEW__ literal\n"


def _scripts(page: str) -> list[str]:
    return re.findall(r"<script>(.*?)</script>", page, re.S)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node for JS parsing")
def test_emitted_page_js_parses(tmp_path):
    page = build_page(NASTY_MD, "t", "/tmp/d.md", "abc123")
    for i, js in enumerate(_scripts(page)):
        p = tmp_path / f"s{i}.js"
        p.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"script #{i} has a JS syntax error:\n{r.stderr}"


def test_no_raw_newline_inside_js_string_literals():
    # node-free backstop for the same bug: a line of emitted JS must never
    # end inside an unterminated single-quoted string.
    page = build_page(NASTY_MD, "t", "/tmp/d.md", "abc123")
    for js in _scripts(page):
        for line in js.splitlines():
            stripped = re.sub(r"'(?:[^'\\]|\\.)*'", "", line)  # drop complete strings
            stripped = stripped.split("//")[0]  # then line comments (apostrophes ok there)
            assert not re.search(r"'(?:[^'\\]|\\.)*$", stripped), (
                f"unterminated JS string literal (raw newline?): {line!r}"
            )


def test_rev_is_injected():
    page = build_page("x", "t", "/tmp/d.md", "deadbeef")
    assert "let rev = 'deadbeef';" in page


def test_live_preview_and_presence_wired_in():
    # The page must carry the debounced-render + presence-chip machinery this
    # feature adds: the /api/render call, the debounce scheduler wired to input,
    # and the presence chip element the pollDisk loop lights up.
    page = build_page("# T\n\nbody\n", "t", "/tmp/d.md", "abc123")
    assert 'id="presence"' in page
    assert "fetch('api/render'" in page
    assert "scheduleRender()" in page
    assert "notePresence()" in page
    assert "cowrite-flash" in page


# ---- anchored comments -------------------------------------------------------

def test_comment_markers_do_not_render_as_text():
    html = render_fragment("A paragraph. <!-- cowrite[daniel]: tighten this -->\n")
    assert "A paragraph." in html
    # the marker must be parsed out, not shown (raw text or lingering comment)
    assert "cowrite" not in html
    assert "tighten this" not in html


def test_strip_comment_markers_keeps_prose():
    assert strip_comment_markers("x <!-- cowrite[a]: y -->\nz").strip() == "x \nz".strip()


def test_analyze_anchors_comment_to_its_block():
    md = "First para. <!-- cowrite[daniel]: fix this -->\n\nSecond para.\n"
    data = analyze_comments(md)
    assert len(data["blocks"]) == 2
    assert len(data["comments"]) == 1
    c = data["comments"][0]
    assert (c["author"], c["text"], c["block"]) == ("daniel", "fix this", 0)
    # offsets point exactly at the marker so the client can splice/delete it
    assert md[c["start"]:c["end"]] == "<!-- cowrite[daniel]: fix this -->"


def test_analyze_block_end_is_appendable():
    md = "Alpha\n\nBeta\n"
    data = analyze_comments(md)
    ends = [b["end"] for b in data["blocks"]]
    assert [md[:ends[0]], md[:ends[1]]] == ["Alpha", "Alpha\n\nBeta"]


def test_analyze_ignores_blank_lines_inside_fences():
    md = "```\ncode\n\nmore\n```\n\nAfter.\n"
    data = analyze_comments(md)
    # the fenced block (with its blank line) is one anchor block, not three
    assert len(data["blocks"]) == 2


def test_marker_only_block_anchors_to_preceding_content():
    md = "A paragraph.\n\n<!-- cowrite[daniel]: standalone -->\n\nNext.\n"
    data = analyze_comments(md)
    # the marker-only block is not itself a content block
    assert len(data["blocks"]) == 2
    assert data["comments"][0]["block"] == 0


def test_loose_list_is_a_single_block():
    md = "- one\n\n- two\n\n- three\n"
    assert len(analyze_comments(md)["blocks"]) == 1


def test_author_defaults_to_daniel_and_is_configurable():
    assert "let COWRITE_AUTHOR = 'daniel';" in build_page("x", "t", "/d.md", "r")
    assert "let COWRITE_AUTHOR = 'alice';" in build_page("x", "t", "/d.md", "r", author="alice")
    # authors are sanitized so they can't break the marker grammar or the JS string
    assert "let COWRITE_AUTHOR = 'ev';" in build_page("x", "t", "/d.md", "r", author="e']v[")


def test_comments_payload_injected_into_page():
    page = build_page("Hi. <!-- cowrite[daniel]: note -->\n", "t", "/d.md", "r")
    assert '"text": "note"' in page or '"text":"note"' in page
    assert "let COWRITE_COMMENTS = [" in page
