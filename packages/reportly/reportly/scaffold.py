"""Scaffold a new report from the standard template.

The template is deliberately a *filled skeleton* that passes ``reportly lint``:
front matter with a vibe, a thesis-shaped H1, the required sections (TL;DR, Setup,
Result with a headline figure, Reproduce), a Discussion, and a provenance footer.
Author replaces the angle-bracket placeholders.
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE = """\
---
vibe: positive          # positive | negative | mixed (the result's vibe)
preliminary: true       # remove once the result is trustworthy
---
# {title}

## TL;DR
<2-4 sentences: what you tested, what you found, and why it matters. Lead with
the result, not the motivation.>

## Setup
<Models, data, configs, seeds — enough that a reader knows exactly what was run.>

## Result
![headline result]({figdir}/{slug}-headline.png)

<Lead with the figure, then state the claim it establishes. Put numbers in a
Markdown table. Add `### sub-results` as separate claims if there are several.>

## Discussion & next steps
<What it means, the caveats and failed controls (report them — don't bury them),
and what you would do next.>

## Reproduce
```bash
# env: <required keys, e.g. TINKER_API_KEY + OPENAI_API_KEY in ~/.env>
uv run python -m <module> ...        # seeds 0,1,2
uv run python -m <module>.make_figure
```

*Branch: `{branch}`. Model: `<model>`. Artifacts: `results/{slug}.jsonl` \
(large artifacts in `gs://alignment-team-general-storage/daniel/jarvis/experiments/{slug}/`). \
Code: `<pkg>/{{run,judge,make_figure}}.py`.*
"""


def _titleize(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


def scaffold(slug: str, reports_dir: str | Path = "reports", *,
             title: str | None = None, figdir: str = "figs",
             branch: str | None = None, force: bool = False) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / figdir).mkdir(exist_ok=True)

    out = reports_dir / f"{slug}.md"
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists (use force=True / --force to overwrite)")

    text = TEMPLATE.format(
        title=title or f"<{_titleize(slug)} — state the finding as a sentence>",
        slug=slug, figdir=figdir, branch=branch or f"experiment/{slug}")
    out.write_text(text)
    return out
