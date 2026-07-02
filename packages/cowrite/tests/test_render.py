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

from cowrite.render import build_page

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
