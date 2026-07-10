"""Tests for reportly: parsing, the standard's rules, scaffold, and build."""
from __future__ import annotations

from pathlib import Path

import pytest

import reportly
from reportly import core, lint
from reportly.config import Config

GOOD = """\
---
vibe: positive
preliminary: true
---
# Installing one synthetic fact makes the model fabricate collateral claims

## Questions
**Q1. Does installing one fact make the model fabricate collateral claims?**
Yes — +32pp fabrication on neighboring claims vs control (Fig 1). High confidence.

**Q2. Does the effect survive seeds and a paraphrase control?**
Yes — holds across 3 seeds; paraphrase control stays at baseline (Table 1). Medium confidence.

## Evidence
![headline](figs/x-headline.png)

| arm | fabrication |
|---|---:|
| installed | 0.41 |
| control | 0.09 |

## What was run
Qwen3-30B, 3 seeds, SFT on a synthetic corpus; paraphrase-control corpus.

## Interpretation
The model treats the install as a general rule, not a single fact.

## Next steps
Test whether ordering the corpus by distance changes the spread.

## Reproduce
```bash
uv run python -m exp.run   # seeds 0,1,2
```

*Branch: `experiment/x`. Model: Qwen3-30B. Artifacts: `results/x.jsonl`. Code: `exp/run.py`.*
"""

Q1_ANSWER = "Yes — +32pp fabrication on neighboring claims vs control (Fig 1). High confidence."
Q2 = "**Q2. Does the effect survive seeds and a paraphrase control?**"
Q2_ANSWER = "Yes — holds across 3 seeds; paraphrase control stays at baseline (Table 1). Medium confidence."


def _write(tmp_path: Path, text: str, name: str = "r.md") -> Path:
    (tmp_path / "figs").mkdir(exist_ok=True)
    (tmp_path / "figs" / "x-headline.png").write_bytes(b"\x89PNG")
    p = tmp_path / name
    p.write_text(text)
    return p


def test_parse_frontmatter_and_anchors(tmp_path):
    r = core.parse(_write(tmp_path, GOOD))
    assert r.meta["vibe"] == "positive"
    assert r.meta["preliminary"] == "true"
    assert r.title.startswith("Installing one synthetic fact")
    norms = {a.norm for a in r.anchors}
    assert "questions" in norms and "evidence" in norms and "what was run" in norms


def test_summary_skips_headings(tmp_path):
    r = core.parse(_write(tmp_path, GOOD))
    assert r.summary.startswith("Q1.")  # the answer sheet, not the word "Questions"


def test_bold_lead_counts_as_anchor(tmp_path):
    text = GOOD.replace("## Interpretation\nThe model treats",
                        "**Interpretation.** The model treats")
    r = core.parse(_write(tmp_path, text))
    assert any(a.norm == "interpretation" for a in r.anchors)
    assert not lint.lint_report(r, Config())  # still passes the standard


def test_good_report_is_clean(tmp_path):
    issues = lint.lint_file(_write(tmp_path, GOOD))
    assert issues == [], [i.format() for i in issues]


def test_discussion_and_next_steps_required(tmp_path):
    no_disc = GOOD.replace(
        "## Interpretation\nThe model treats the install as a general rule, not a single fact.\n\n", "")
    issues = lint.lint_file(_write(tmp_path, no_disc))
    assert any(i.rule == "required_sections" and "discussion" in i.message for i in issues)

    no_next = GOOD.replace(
        "## Next steps\nTest whether ordering the corpus by distance changes the spread.\n\n", "")
    issues = lint.lint_file(_write(tmp_path, no_next))
    assert any(i.rule == "required_sections" and "next_steps" in i.message for i in issues)


def test_combined_interpretation_next_steps_heading_satisfies_both(tmp_path):
    combined = GOOD.replace(
        "## Interpretation\nThe model treats the install as a general rule, not a single fact.\n\n"
        "## Next steps\nTest whether ordering the corpus by distance changes the spread.\n",
        "## Interpretation & next steps\nIt generalizes; next, reorder the corpus by distance.\n")
    assert not any(i.rule == "required_sections"
                   for i in lint.lint_file(_write(tmp_path, combined)))


def test_missing_required_section_is_error(tmp_path):
    text = GOOD.replace("## Reproduce\n```bash\nuv run python -m exp.run   # seeds 0,1,2\n```\n", "")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "required_sections" and i.severity == lint.ERROR for i in issues)


def test_missing_questions_section_is_error(tmp_path):
    text = GOOD.replace(
        "## Questions\n"
        f"**Q1. Does installing one fact make the model fabricate collateral claims?**\n"
        f"{Q1_ANSWER}\n\n{Q2}\n{Q2_ANSWER}\n\n", "")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "required_sections" and "questions" in i.message
               and i.severity == lint.ERROR for i in issues)


def test_unanswered_question_is_error(tmp_path):
    text = GOOD.replace(f"{Q2}\n{Q2_ANSWER}", Q2)  # bold question, no answer beneath
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "questions_answered" and i.severity == lint.ERROR
               and "unanswered" in i.message for i in issues)


def test_answer_in_next_paragraph_counts_as_unanswered(tmp_path):
    # the blank line between Q and A breaks the lintable pairing
    text = GOOD.replace(f"{Q2}\n{Q2_ANSWER}", f"{Q2}\n\n{Q2_ANSWER}")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "questions_answered" and i.severity == lint.ERROR for i in issues)


def test_questions_section_without_items_is_error(tmp_path):
    text = GOOD.replace(
        f"**Q1. Does installing one fact make the model fabricate collateral claims?**\n"
        f"{Q1_ANSWER}\n\n{Q2}\n{Q2_ANSWER}",
        "We asked whether one installed fact spreads to neighbors.")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "questions_answered" and i.severity == lint.ERROR
               and "no" in i.message for i in issues)


def test_answer_without_evidence_pointer_warns(tmp_path):
    text = GOOD.replace(Q1_ANSWER, "Yes, definitely.")
    issues = lint.lint_file(_write(tmp_path, text))
    hits = [i for i in issues if i.rule == "questions_answered"]
    assert hits and all(i.severity == lint.WARN for i in hits)
    assert not lint.is_failure(issues, Config())  # warn-only at level=error


def test_not_answered_is_a_valid_answer(tmp_path):
    text = GOOD.replace(Q2_ANSWER, "Not answered — the paraphrase-control run collapsed.")
    issues = lint.lint_file(_write(tmp_path, text))
    assert not any(i.rule == "questions_answered" for i in issues)


def test_paper_order_warns(tmp_path):
    setup_block = "## What was run\nQwen3-30B, 3 seeds, SFT on a synthetic corpus; paraphrase-control corpus.\n\n"
    text = GOOD.replace(setup_block, "").replace("## Questions\n", setup_block + "## Questions\n")
    issues = lint.lint_file(_write(tmp_path, text))
    hits = [i for i in issues if i.rule == "answers_first"]
    assert hits and hits[0].severity == lint.WARN


def test_bad_vibe_is_error(tmp_path):
    text = GOOD.replace("vibe: positive", "vibe: amazing")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "frontmatter" and i.severity == lint.ERROR for i in issues)


def test_missing_figure_is_error(tmp_path):
    text = GOOD.replace("figs/x-headline.png", "figs/does-not-exist.png")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "figures_exist" and i.severity == lint.ERROR for i in issues)


def test_topic_title_warns(tmp_path):
    text = GOOD.replace(
        "# Installing one synthetic fact makes the model fabricate collateral claims",
        "# Collateral hallucination")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "thesis_h1" and i.severity == lint.WARN for i in issues)


def test_evidence_with_table_only_satisfies_evidence(tmp_path):
    text = GOOD.replace("![headline](figs/x-headline.png)\n\n", "")
    issues = lint.lint_file(_write(tmp_path, text))
    assert not any(i.rule == "result_figure" for i in issues)


def test_missing_footer_warns_not_errors(tmp_path):
    text = GOOD.rsplit("\n*Branch:", 1)[0] + "\n"
    issues = lint.lint_file(_write(tmp_path, text))
    foot = [i for i in issues if i.rule == "provenance_footer"]
    assert foot and foot[0].severity == lint.WARN
    assert not lint.is_failure(issues, Config())  # warn-only -> passes at level=error
    assert lint.is_failure(issues, Config(level="warn"))  # ...fails at level=warn


def test_config_can_relax_required(tmp_path):
    text = GOOD.replace("## Reproduce\n```bash\nuv run python -m exp.run   # seeds 0,1,2\n```\n", "")
    cfg = Config(required=["questions", "setup", "result"])  # reproduce no longer required
    assert not any(i.rule == "required_sections" for i in lint.lint_file(_write(tmp_path, text), cfg))


def test_disable_rule(tmp_path):
    text = GOOD.rsplit("\n*Branch:", 1)[0] + "\n"
    cfg = Config(disable=["provenance_footer"])
    assert not any(i.rule == "provenance_footer" for i in lint.lint_file(_write(tmp_path, text), cfg))


def test_scaffold_then_lint_is_clean(tmp_path):
    out = reportly.scaffold("my-exp", tmp_path / "reports")
    (tmp_path / "reports" / "figs" / "my-exp-headline.png").write_bytes(b"\x89PNG")
    issues = lint.lint_file(out)
    assert issues == [], [i.format() for i in issues]


def test_scaffold_with_spec_questions(tmp_path):
    out = reportly.scaffold("depth", tmp_path / "reports",
                            questions=["Does backdoor depth change durability",
                                       "Is the late-layer refuge scale-dependent?"])
    text = out.read_text()
    assert "**Q1. Does backdoor depth change durability?**" in text
    assert "**Q2. Is the late-layer refuge scale-dependent?**" in text


def test_build_renders_site(tmp_path):
    reports = tmp_path / "reports"
    _write(reports, GOOD, "r.md") if reports.mkdir() or True else None
    out = reportly.build(reports)
    assert (out / "index.html").exists()
    assert (out / "r.html").exists()
    assert "Installing one synthetic fact" in (out / "index.html").read_text()


def test_code_fence_headings_ignored(tmp_path):
    """A '## Setup' inside a code fence must not satisfy the Setup requirement."""
    text = GOOD.replace(
        "## What was run\nQwen3-30B, 3 seeds, SFT on a synthetic corpus; paraphrase-control corpus.\n\n", "")
    text = text.replace("uv run python -m exp.run   # seeds 0,1,2",
                        "uv run python -m exp.run\n## Setup not a real heading")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "required_sections" and "setup" in i.message for i in issues)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
