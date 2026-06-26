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

## TL;DR
We installed a fact and the model fabricated that others did it too.

## Setup
Qwen3-30B, 3 seeds, SFT on a synthetic corpus.

## Result
![headline](figs/x-headline.png)

The effect is large and decays with distance.

## Discussion
The model treats the install as a general rule, not a single fact.

## Next steps
Test whether ordering the corpus by distance changes the spread.

## Reproduce
```bash
uv run python -m exp.run   # seeds 0,1,2
```

*Branch: `experiment/x`. Model: Qwen3-30B. Artifacts: `results/x.jsonl`. Code: `exp/run.py`.*
"""


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
    assert "tl;dr" in norms and "setup" in norms and "result" in norms


def test_bold_lead_counts_as_anchor(tmp_path):
    text = GOOD.replace("## TL;DR\nWe installed", "**TL;DR.** We installed")
    r = core.parse(_write(tmp_path, text))
    assert any("tl;dr" in a.norm for a in r.anchors)
    assert not lint.lint_report(r, Config())  # still passes the standard


def test_good_report_is_clean(tmp_path):
    issues = lint.lint_file(_write(tmp_path, GOOD))
    assert issues == [], [i.format() for i in issues]


def test_discussion_and_next_steps_required(tmp_path):
    no_disc = GOOD.replace("## Discussion\nThe model treats the install as a general rule, not a single fact.\n\n", "")
    issues = lint.lint_file(_write(tmp_path, no_disc))
    assert any(i.rule == "required_sections" and "discussion" in i.message for i in issues)

    no_next = GOOD.replace("## Next steps\nTest whether ordering the corpus by distance changes the spread.\n\n", "")
    issues = lint.lint_file(_write(tmp_path, no_next))
    assert any(i.rule == "required_sections" and "next_steps" in i.message for i in issues)


def test_combined_discussion_next_steps_heading_satisfies_both(tmp_path):
    combined = GOOD.replace(
        "## Discussion\nThe model treats the install as a general rule, not a single fact.\n\n"
        "## Next steps\nTest whether ordering the corpus by distance changes the spread.\n",
        "## Discussion & next steps\nIt generalizes; next, reorder the corpus by distance.\n")
    assert not any(i.rule == "required_sections"
                   for i in lint.lint_file(_write(tmp_path, combined)))


def test_missing_required_section_is_error(tmp_path):
    text = GOOD.replace("## Reproduce\n```bash\nuv run python -m exp.run   # seeds 0,1,2\n```\n", "")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "required_sections" and i.severity == lint.ERROR for i in issues)


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


def test_result_with_table_satisfies_evidence(tmp_path):
    text = GOOD.replace(
        "![headline](figs/x-headline.png)\n\nThe effect is large and decays with distance.",
        "| arm | rate |\n|---|---:|\n| base | 0.1 |\n")
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
    cfg = Config(required=["tldr", "setup", "result"])  # reproduce no longer required
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


def test_build_renders_site(tmp_path):
    reports = tmp_path / "reports"
    _write(reports, GOOD, "r.md") if reports.mkdir() or True else None
    out = reportly.build(reports)
    assert (out / "index.html").exists()
    assert (out / "r.html").exists()
    assert "Installing one synthetic fact" in (out / "index.html").read_text()


def test_code_fence_headings_ignored(tmp_path):
    """A '## Setup' inside a code fence must not satisfy the Setup requirement."""
    text = GOOD.replace("## Setup\nQwen3-30B, 3 seeds, SFT on a synthetic corpus.\n\n", "")
    text = text.replace("uv run python -m exp.run   # seeds 0,1,2",
                        "uv run python -m exp.run\n## Setup not a real heading")
    issues = lint.lint_file(_write(tmp_path, text))
    assert any(i.rule == "required_sections" and "setup" in i.message for i in issues)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
