# arxivist

arXiv papers as structured, **agent-legible** markdown.

`arxivist` downloads a paper and parses it into a clean section tree with real
LaTeX math, figure/table captions, and a reference list — so a coding agent
can read a paper the way it reads code: outline first, then just the sections
it needs.

## How it gets the content

1. **arXiv native HTML first** (LaTeXML rendering, available for most papers
   submitted since late 2023). This preserves the true document structure, and
   every formula's original LaTeX is recovered from the MathML `alttext` — no
   glyph soup.
2. **PDF fallback** (`pymupdf4llm`) for papers without an HTML rendering; the
   section tree is reconstructed from heading heuristics.

Metadata (title, authors, abstract, categories, dates) always comes from the
arXiv export API. Everything — raw downloads and parsed results — is cached
under `~/.cache/arxivist/` (override with `ARXIVIST_CACHE`).

## CLI (agent-first)

```bash
arxivist abstract 2401.05566        # title + authors + abstract (metadata only, cheap)
arxivist outline 2401.05566        # TOC with per-section word counts — budget your read
arxivist section 2401.05566 3.2    # one section, by number or title substring
arxivist refs 2401.05566           # reference list
arxivist get 2401.05566            # full paper as markdown on stdout
arxivist get 2401.05566 -o papers/ # writes papers/<id>/paper.md + paper.json
arxivist get 2401.05566 --json     # structured JSON (section tree) on stdout
```

IDs may be bare (`2401.05566`), versioned (`2401.05566v2`), old-style
(`hep-th/9901001`), or any arxiv.org URL (`/abs/`, `/pdf/`, `/html/`).
`--refresh` bypasses the cache; `get --pdf` forces the PDF parser.

## Python API

```python
import arxivist

paper = arxivist.get("2401.05566")          # Paper
print(paper.outline())                       # numbered TOC + word counts
sec = paper.get_section("3.2")               # or by title: "related work"
print(sec.content)                           # markdown, math as $...$ LaTeX
paper.save("papers/sleeper-agents")          # paper.md + paper.json

meta = arxivist.get_metadata("2401.05566")   # metadata only, no body download
```

`Paper` is a plain dataclass (`to_dict` / `from_dict` / `save` / `load`), so
parsed papers round-trip through JSON for downstream pipelines.

## Install

Part of the [arsenal](../../README.md) workspace: `uv sync --all-packages` at
the repo root. Standalone: `pip install packages/arxivist`.

## Tests

```bash
uv run pytest packages/arxivist/tests -q          # hermetic (fixtures only)
RUN_LIVE=1 uv run pytest packages/arxivist/tests  # + live arxiv.org round-trips
```
