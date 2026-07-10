"""Scaffold a new report from the standard template.

The template is deliberately a *filled skeleton* that passes ``reportly lint``:
front matter with a vibe, a thesis-shaped H1, the answer sheet (a Questions
section with in-place answers), Evidence with a headline figure, What was run,
Interpretation, Next steps, Reproduce, and a provenance footer. The content
standard this skeleton serializes lives in ``REPORTING.md``. The author replaces
the angle-bracket placeholders; questions should be lifted from the experiment
spec (pass them via ``questions=`` / ``reportly new -q``).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

TEMPLATE = """\
---
vibe: positive          # positive | negative | mixed (the result's vibe)
preliminary: true       # remove once the result is trustworthy
---
# {title}

## Questions
<Fixed at design time — lift them from the experiment spec (universal baseline
set in REPORTING.md; keep the ones that make sense here). A spec question may
not be silently dropped — "Not answered — <why>" is a valid answer. Questions
added at writeup time get *(post-hoc)*.>

{questions}

## Evidence
![headline result]({figdir}/{slug}-headline.png)

<One figure or table per claim, tagged with the question it answers (Q1, Q2, …)
and captioned with the claim it establishes. Numbers go in Markdown tables, not
prose. No narrative.>

## What was run
<Only what a reader needs to interpret the evidence: models, data, seeds, the
knobs that matter, the controls. Not a methods narrative; no background.>

## Interpretation
<Only after the evidence: takeaways, caveats and failed controls (report them —
don't bury them), surprises, what would change the conclusion, and deviations
("we set out to answer X, we actually answered X'").>

## Next steps
<What you would do next — concrete follow-up experiments or open questions.>

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

ANSWER_PLACEHOLDER = (
    "<Answer in 1-3 lines: direction + magnitude with the actual number, the "
    "evidence pointer (Fig 1 / Table 1), and confidence high/medium/low.>")

# Placeholders for the two baseline questions every spec tends to carry; real
# questions should come from the spec (see REPORTING.md for the full set).
DEFAULT_QUESTIONS: tuple[str, ...] = (
    "<Headline — what is the effect of <X> on <Y>, direction and magnitude?>",
    "<Reality — is it real: does it survive seeds, controls, and the obvious confound?>",
)


def _questions_block(questions: Sequence[str]) -> str:
    items = []
    for i, q in enumerate(questions, 1):
        q = q.strip()
        if "?" not in q:
            q += "?"
        items.append(f"**Q{i}. {q}**\n{ANSWER_PLACEHOLDER}")
    return "\n\n".join(items)


def _titleize(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().capitalize()


def scaffold(slug: str, reports_dir: str | Path = "reports", *,
             title: str | None = None, figdir: str = "figs",
             branch: str | None = None, questions: Sequence[str] | None = None,
             force: bool = False) -> Path:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / figdir).mkdir(exist_ok=True)

    out = reports_dir / f"{slug}.md"
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists (use force=True / --force to overwrite)")

    text = TEMPLATE.format(
        title=title or f"<{_titleize(slug)} — state the finding as a sentence>",
        slug=slug, figdir=figdir, branch=branch or f"experiment/{slug}",
        questions=_questions_block(questions or DEFAULT_QUESTIONS))
    out.write_text(text)
    return out
