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


def test_formatting_shortcuts_wired():
    # ⌘/Ctrl + B/I/K must be bound to the textarea string-editing helpers.
    page = build_page("body", "t", "/tmp/d.md", "abc123")
    assert "wrapSelection('**')" in page  # bold
    assert "wrapSelection('*')" in page  # italic
    assert "insertLink()" in page  # link
    assert "'](url)'" in page  # link inserts a url placeholder
    for key in ("'b'", "'i'", "'k'"):
        assert f"k === {key}" in page, f"missing keybinding for {key}"


def test_formatting_uses_execcommand_for_undo():
    # The edits route through execCommand so they join the native undo stack;
    # setRangeText is only the offline fallback.
    page = build_page("body", "t", "/tmp/d.md", "abc123")
    assert "document.execCommand('insertText'" in page
    assert "setRangeText" in page


def test_hint_advertises_formatting():
    page = build_page("body", "t", "/tmp/d.md", "abc123")
    assert "B/I/C" not in page  # sanity: the hint is B/I/K
    assert "B/I/K format" in page


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wrap_and_link_behavior_in_node(tmp_path):
    # Exercise the actual formatting helpers with a minimal textarea stub, so a
    # logic regression (offsets, toggle, url placement) fails a test, not just
    # a syntax error. We pull the helper source out of the emitted page and run
    # it under node against a fake `src`/`document`.
    page = build_page("body", "t", "/tmp/d.md", "abc123")
    src_js = "\n".join(_scripts(page))
    lo = src_js.index("function srcEdit(")
    hi = src_js.index("src.addEventListener('keydown', (e) => {\n  if (!(e.metaKey")
    helpers = src_js[lo:hi]

    harness = (
        "let value='', selectionStart=0, selectionEnd=0;\n"
        "const src={focus(){}, setRangeText(t,s,e){value=value.slice(0,s)+t+value.slice(e); "
        "this.selectionStart=this.selectionEnd=s+t.length;},\n"
        "  get value(){return value;}, set value(v){value=v;},\n"
        "  get selectionStart(){return selectionStart;}, set selectionStart(v){selectionStart=v;},\n"
        "  get selectionEnd(){return selectionEnd;}, set selectionEnd(v){selectionEnd=v;}};\n"
        "const document={execCommand(_c,_u,t){value=value.slice(0,selectionStart)+t+value.slice(selectionEnd);"
        "selectionStart=selectionEnd=selectionStart+t.length; return true;}};\n"
        "function markDirty(){}\n"
        + helpers
        + "\n"
        # wrap a selected word in bold
        "value='hi there'; selectionStart=3; selectionEnd=8; wrapSelection('**');\n"
        "if (value !== 'hi **there**') throw new Error('bold wrap: '+JSON.stringify(value));\n"
        "if (value.slice(selectionStart,selectionEnd) !== 'there') throw new Error('bold sel');\n"
        # toggle it back off (markers now outside the same selection)
        "wrapSelection('**');\n"
        "if (value !== 'hi there') throw new Error('bold unwrap: '+JSON.stringify(value));\n"
        # italic on already-bold text nests to bold+italic, it must not chew a
        # layer off the '**' markers (the guard that keeps * from unwrapping **)
        "value='a **b** c'; selectionStart=4; selectionEnd=5; wrapSelection('*');\n"
        "if (value !== 'a ***b*** c') throw new Error('italic-vs-bold: '+JSON.stringify(value));\n"
        # link inserts [sel](url) with the url pre-selected
        "value='see docs'; selectionStart=4; selectionEnd=8; insertLink();\n"
        "if (value !== 'see [docs](url)') throw new Error('link: '+JSON.stringify(value));\n"
        "if (value.slice(selectionStart,selectionEnd) !== 'url') throw new Error('url sel');\n"
        "console.log('ok');\n"
    )
    p = tmp_path / "behavior.js"
    p.write_text(harness, encoding="utf-8")
    r = subprocess.run(["node", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
