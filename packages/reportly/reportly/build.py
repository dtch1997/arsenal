"""Render a reports directory into a static site: one styled ``<stem>.html`` per
report plus an ``index.html`` of cards (title + one-line summary + vibe badges).

Generalized from model-thrashing's ``reports/build_index.py``: the site title and
subtitle come from config (or sensible defaults), and front-matter badges use the
configured vibe vocabulary. Figures referenced relatively (``figs/x.png``) resolve
because they live under the reports dir, which is the site root.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import core
from .config import Config

PAGE_CSS = """
:root { color-scheme: light dark; }
body { max-width: 820px; margin: 2.5rem auto; padding: 0 1.2rem;
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1c1e21; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #e4e6eb; background: #18191a; } }
h1, h2, h3 { line-height: 1.25; }
h1 { font-size: 1.9rem; } h2 { margin-top: 2rem; border-bottom: 1px solid #8884; padding-bottom: .2rem; }
img { max-width: 100%; height: auto; display: block; margin: 1rem auto; border-radius: 6px; }
code { background: #8881; padding: .12em .35em; border-radius: 4px; font-size: .92em; }
pre code { display: block; padding: .8rem; overflow-x: auto; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #8886; padding: .35rem .7rem; text-align: left; }
th { background: #8881; }
a { color: #2d6cdf; } blockquote { border-left: 3px solid #8886; margin: 1rem 0; padding: .2rem 1rem; color: #8a8d91; }
.back { font-size: .9rem; } .meta { color: #8a8d91; font-size: .92rem; }
.card { border: 1px solid #8884; border-radius: 8px; padding: 1rem 1.2rem; margin: 1rem 0; }
.card h2 { border: 0; margin: 0 0 .3rem; font-size: 1.25rem; }
.card a { text-decoration: none; }
.badges { margin: .3rem 0 1rem; display: flex; flex-wrap: wrap; gap: .4rem; }
.card .badges { margin: .1rem 0 .4rem; }
.badge { display: inline-block; font-size: .72rem; font-weight: 700; line-height: 1.4;
  padding: .1em .6em; border-radius: 999px; text-transform: uppercase; letter-spacing: .04em; }
.badge-positive { background: #1a7f37; color: #fff; }
.badge-negative { background: #cf222e; color: #fff; }
.badge-mixed { background: #bf8700; color: #fff; }
.badge-preliminary { background: transparent; color: #8a8d91; border: 1px solid #8888; }
"""

PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>{title}</title><style>{css}</style></head><body>{body}</body></html>')


def _md():
    from markdown_it import MarkdownIt
    return MarkdownIt("default")  # tables + GFM-ish extras


def _badges_html(meta: dict, config: Config) -> str:
    pills = []
    vibe = meta.get("vibe", "").strip().lower()
    if vibe in [v.lower() for v in config.vibe_values]:
        cls = vibe if vibe in ("positive", "negative", "mixed") else "mixed"
        pills.append(f'<span class="badge badge-{cls}">{html.escape(vibe)}</span>')
    if str(meta.get("preliminary", "")).strip().lower() in ("true", "yes", "1"):
        pills.append('<span class="badge badge-preliminary">preliminary</span>')
    return f'<div class="badges">{"".join(pills)}</div>' if pills else ""


def _render_report(path: Path, out_dir: Path, config: Config, md) -> dict:
    report = core.parse(path)
    body_html = md.render(report.body)
    nav = '<p class="back"><a href="index.html">&larr; all reports</a></p>'
    badges = _badges_html(report.meta, config)
    title = report.title or path.stem
    out = out_dir / (path.stem + ".html")
    out.write_text(PAGE.format(title=html.escape(title), css=PAGE_CSS,
                               body=nav + badges + body_html))
    return {"html": out.name, "title": title, "summary": report.summary, "badges": badges}


def build(reports_dir: str | Path, out_dir: str | Path | None = None,
          config: Config | None = None) -> Path:
    """Render every ``*.md`` (except README) into ``out_dir`` (defaults in-place)."""
    config = config or Config()
    reports_dir = Path(reports_dir)
    out_dir = Path(out_dir) if out_dir is not None else reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    md = _md()

    md_files = sorted(p for p in reports_dir.glob("*.md") if p.name.lower() != "readme.md")
    rendered = [_render_report(p, out_dir, config, md) for p in md_files]

    title = config.site_title or f"{reports_dir.resolve().parent.name} — reports"
    subtitle = config.site_subtitle or (
        "Self-contained write-ups of experiments. Each contributes a report here.")

    cards = [
        f'<div class="card"><h2><a href="{html.escape(r["html"])}">'
        f'{html.escape(r["title"])}</a></h2>{r["badges"]}'
        f'<p class="meta">{html.escape(r["summary"])}</p></div>'
        for r in rendered
    ]
    body = (f"<h1>{html.escape(title)}</h1>"
            f'<p class="meta">{html.escape(subtitle)}</p>'
            + ("".join(cards) if cards else "<p>No reports yet.</p>"))
    (out_dir / "index.html").write_text(
        PAGE.format(title=html.escape(title), css=PAGE_CSS, body=body))
    return out_dir
