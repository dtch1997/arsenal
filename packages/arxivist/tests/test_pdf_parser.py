from arxivist.metadata import PaperMetadata
from arxivist.pdf_parser import parse_pdf_markdown

MD = """\
Some Title

Author One, Author Two

Abstract text here.

# 1 Introduction

Deep learning is popular.

## 1.1 Background

Prior work.

# 2 Method

We do things.

```
# not a heading: inside code fence
```

# References

[1] A. Author. Great paper. 2020.
[2] B. Author. Another paper. 2021.
"""

META = PaperMetadata(
    arxiv_id="1706.99999", version=None, title="Some Title", abstract="Abstract text here."
)


def _paper():
    return parse_pdf_markdown(MD, META)


def test_tree_and_front_matter():
    paper = _paper()
    assert paper.source == "pdf"
    titles = [s.title for s in paper.sections]
    assert titles == ["(front matter)", "Introduction", "Method"]
    intro = paper.get_section("1")
    assert intro.children[0].title == "Background"
    assert intro.children[0].number == "1.1"
    assert "# not a heading" in paper.get_section("Method").content


def test_references_extracted():
    paper = _paper()
    assert [r.label for r in paper.references] == ["1", "2"]
    assert "Another paper" in paper.references[1].text
    assert paper.get_section("References") is None  # popped out of the tree


def test_unnumbered_headings():
    md = "# Introduction\n\nhello\n\n# Acknowledgments\n\nthanks"
    paper = parse_pdf_markdown(md, META)
    assert [s.number for s in paper.sections] == [None, None]
    assert paper.get_section("acknow").content == "thanks"
