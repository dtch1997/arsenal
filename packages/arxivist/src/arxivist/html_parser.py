"""Parse arXiv's native LaTeXML HTML rendering into a :class:`Paper`.

This is the preferred path: arXiv has rendered most papers submitted since
late 2023 to HTML via LaTeXML, which preserves the real document structure
(section tree, figure/table captions, bibliography) and — crucially for
agent legibility — carries the original LaTeX of every formula in the
``alttext`` attribute of each ``<math>`` element. We recover that, so math
comes out as ``$...$`` / ``$$...$$`` LaTeX rather than mangled glyph soup.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from .metadata import PaperMetadata
from .models import Figure, Paper, Reference, Section

# Section-like containers, in nesting order. ltx_paragraph (\paragraph{})
# is treated as content, not a section: its titles are run-in.
_SECTION_CLASSES = ("ltx_section", "ltx_subsection", "ltx_subsubsection", "ltx_appendix")

# Inline elements dropped entirely (anchors, error spans, footnote marks...).
_SKIP_INLINE = {
    "ltx_ERROR",
    "ltx_note_mark",
    "ltx_tag_item",  # list bullets; markdown provides its own
    "ltx_rule",
    "ltx_pagination",
}


def _classes(el: Tag) -> set[str]:
    return set(el.get("class") or [])


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class _Converter:
    def __init__(self) -> None:
        self.figures: list[Figure] = []

    # ------------------------------------------------------------- inline

    def inline(self, node) -> str:
        if isinstance(node, NavigableString):
            return _squash(str(node))
        if not isinstance(node, Tag):
            return ""
        cls = _classes(node)
        if cls & _SKIP_INLINE:
            return ""
        if node.name == "math":
            return self._math(node)
        if node.name == "br":
            return " "
        if node.name in ("script", "style"):
            return ""
        if "ltx_note" in cls:  # footnotes: inline them, clearly delimited
            content = node.find(class_="ltx_note_content")
            if content is not None:
                inner = "".join(
                    self.inline(c)
                    for c in content.children
                    if not (isinstance(c, Tag) and _classes(c) & {"ltx_note_mark", "ltx_tag_note"})
                )
                return f" (footnote: {inner.strip()})"
            return ""
        if node.name == "a":
            inner = "".join(self.inline(c) for c in node.children)
            href = node.get("href", "")
            if href.startswith(("http://", "https://")) and "ltx_href" in cls:
                return f"[{inner}]({href})"
            return inner  # internal ref: keep its text ("Figure 2", "3.1", "1")
        inner = "".join(self.inline(c) for c in node.children)
        if node.name == "cite":
            return inner
        if node.name in ("em", "i") or "ltx_font_italic" in cls:
            s = inner.strip()
            return f" *{s}* " if s else ""
        if node.name in ("strong", "b") or "ltx_font_bold" in cls:
            s = inner.strip()
            return f" **{s}** " if s else ""
        if node.name == "code" or "ltx_font_typewriter" in cls:
            s = inner.strip()
            return f" `{s}` " if s else ""
        if node.name == "sup":
            return f"^{inner.strip()}" if inner.strip() else ""
        if node.name == "sub":
            return f"_{inner.strip()}" if inner.strip() else ""
        return inner

    def _math(self, el: Tag) -> str:
        latex = (el.get("alttext") or _squash(el.get_text())).strip()
        if not latex:
            return ""
        # display math is handled at block level; inline here
        return f"${latex}$"

    def _inline_join(self, el: Tag) -> str:
        text = "".join(self.inline(c) for c in el.children)
        return _squash(text).strip()

    # -------------------------------------------------------------- blocks

    def blocks(self, el: Tag) -> list[str]:
        """Convert a block-level element into a list of markdown paragraphs."""
        cls = _classes(el)
        if el.name == "section":
            if cls & set(_SECTION_CLASSES):
                return []  # structural: handled by the section walker
            # non-structural section (\paragraph{}, theorem, proof, ...):
            # render its title as a bold lead-in and its body inline
            heading = el.find(re.compile(r"^h[1-6]$"), class_="ltx_title", recursive=False)
            title = self._inline_join(heading) if heading is not None else ""
            out = []
            for child in el.children:
                if isinstance(child, Tag) and child is not heading:
                    out.extend(self.blocks(child))
            if title:
                out.insert(0, f"**{title.rstrip('.:')}.**")
            return out
        if re.fullmatch(r"h[1-6]", el.name or "") and "ltx_title" in cls:
            return []  # headings are handled by the section walker
        if {"ltx_equation", "ltx_equationgroup"} & cls:
            return self._equation(el)
        if el.name == "figure":
            return self._figure(el)
        if el.name in ("ul", "ol"):
            return [self._list(el)]
        if el.name == "table":
            return [self._table(el)]
        if el.name == "pre" or "ltx_listing" in cls:
            code = el.get_text().rstrip()
            return [f"```\n{code}\n```"] if code else []
        if el.name == "blockquote" or "ltx_quote" in cls:
            inner: list[str] = []
            for child in el.children:
                if isinstance(child, Tag):
                    inner.extend(self.blocks(child))
                elif str(child).strip():
                    inner.append(_squash(str(child)).strip())
            return ["\n".join(f"> {line}" for para in inner for line in para.splitlines())]
        if el.name == "p":
            text = self._inline_join(el)
            return [text] if text else []
        if el.name in ("div", "span", "article", "li", "dd", "dt"):
            # container: some (ltx_para) hold p children, others nest further
            out: list[str] = []
            pending_inline: list[str] = []

            def flush() -> None:
                text = _squash("".join(pending_inline)).strip()
                pending_inline.clear()
                if text:
                    out.append(text)

            for child in el.children:
                if isinstance(child, Tag) and (
                    child.name in ("div", "p", "figure", "table", "ul", "ol", "pre",
                                   "section", "blockquote")
                    or _classes(child) & {"ltx_equation", "ltx_equationgroup", "ltx_listing"}
                ):
                    flush()
                    out.extend(self.blocks(child))
                else:
                    pending_inline.append(self.inline(child))
            flush()
            return out
        text = self._inline_join(el)
        return [text] if text else []

    def _equation(self, el: Tag) -> list[str]:
        maths = [
            (m.get("alttext") or _squash(m.get_text())).strip()
            for m in el.find_all("math")
            if (m.get("alttext") or m.get_text().strip())
        ]
        if not maths:
            return []
        tag = el.find("span", class_="ltx_tag_equation")
        suffix = f"  {_squash(tag.get_text()).strip()}" if tag else ""
        body = " \\\\\n".join(dict.fromkeys(maths))  # dedupe repeated alttexts
        return [f"$${body}$$" + suffix]

    def _figure(self, el: Tag) -> list[str]:
        cls = _classes(el)
        out: list[str] = []
        if "ltx_table" in cls:
            for t in el.find_all("table"):
                out.append(self._table(t))
        caption = el.find("figcaption")
        if caption is not None:
            tag = caption.find("span", class_="ltx_tag")
            label = _squash(tag.get_text()).strip().rstrip(":. ") if tag else None
            if tag:
                tag.extract()
            text = self._inline_join(caption)
            self.figures.append(Figure(label=label, caption=text))
            shown = f"**{label}:** {text}" if label else f"**Caption:** {text}"
            out.append(shown)
        # nested subfigures already covered by find_all above; skip images
        return out

    def _list(self, el: Tag) -> str:
        ordered = el.name == "ol"
        lines: list[str] = []
        for i, li in enumerate(el.find_all("li", recursive=False), start=1):
            marker = f"{i}." if ordered else "-"
            paras = self.blocks(li)
            if not paras:
                continue
            first, *rest = paras
            lines.append(f"{marker} {first}")
            lines.extend(f"  {p}" for p in rest)
        return "\n".join(lines)

    def _table(self, el: Tag) -> str:
        rows: list[list[str]] = []
        for tr in el.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if cells:
                rows.append([self._inline_join(c) or " " for c in cells])
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [" "] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(lines)

    # ------------------------------------------------------------ sections

    def section(self, el: Tag) -> Section:
        number: str | None = None
        title = ""
        heading = el.find(re.compile(r"^h[1-6]$"), class_="ltx_title", recursive=False)
        if heading is not None:
            tag = heading.find("span", class_="ltx_tag")
            if tag is not None:
                number = _squash(tag.get_text()).strip().rstrip(". ") or None
                tag.extract()
            title = self._inline_join(heading)
        content: list[str] = []
        children: list[Section] = []
        for child in el.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "section" and _classes(child) & set(_SECTION_CLASSES):
                children.append(self.section(child))
            elif child is not heading:
                content.extend(self.blocks(child))
        return Section(
            title=title or "(untitled)",
            number=number,
            content="\n\n".join(content),
            children=children,
        )


def _references(soup: BeautifulSoup) -> list[Reference]:
    refs: list[Reference] = []
    for li in soup.find_all("li", class_="ltx_bibitem"):
        tag = li.find("span", class_="ltx_tag")
        label = _squash(tag.get_text()).strip().strip("[]") if tag else str(len(refs) + 1)
        if tag:
            tag.extract()
        text = _squash(li.get_text(" ")).strip()
        refs.append(Reference(key=li.get("id") or f"ref-{label}", label=label, text=text))
    return refs


def parse_html(html: str, meta: PaperMetadata) -> Paper:
    soup = BeautifulSoup(html, "html.parser")
    conv = _Converter()

    article = soup.find("article") or soup.body or soup
    sections = [
        conv.section(el)
        for el in article.find_all("section", recursive=True)
        if _classes(el) & set(_SECTION_CLASSES)
        # only top-level sections: nested ones are handled recursively
        and not (el.parent is not None and isinstance(el.parent, Tag)
                 and el.find_parent("section", class_=list(_SECTION_CLASSES)))
    ]

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
        source="html",
        sections=sections,
        references=_references(soup),
        figures=conv.figures,
    )
