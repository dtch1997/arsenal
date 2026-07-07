# reportly

A small library + CLI that enforces a consistent **standard for experiment
reports**: scaffold a new report from a template, lint reports against the
standard, and build a static report site. It abstracts the conventions we already
follow by hand (e.g. in `model-thrashing`) so every write-up has the same shape
and the same reproducibility guarantees.

It defines and *enforces* the standard. Writing the prose and serving it stay
where they already are — the `experiment-report` agent drafts reports, and
`cowrite` / `report-viewer` serve them.

## The standard

The **content standard** lives in [REPORTING.md](REPORTING.md): a report is an
*answer sheet* — questions fixed at design time (lifted from the experiment
spec), answered up front with evidence pointers, evidence before interpretation,
written for a reader who already knows the project. `reportly` enforces the
mechanical skeleton derived from it (all overridable via `reportly.toml`):

- **Front matter** — `vibe: positive | negative | mixed` (+ optional
  `preliminary: true`), rendered as badges.
- **H1 reads as a finding**, not a topic label — a sentence stating the result.
- **Required sections** — Questions · Evidence · What was run · Interpretation ·
  Next steps · Reproduce. A section is matched by a heading *or* a bold lead,
  and old-convention names keep working via aliases (Result/Finding ⇢ Evidence,
  Setup/Method ⇢ What was run, Discussion ⇢ Interpretation); a combined
  `Interpretation & next steps` heading satisfies both.
- **Questions is an answer sheet** — at least one `**Qn. …?**` item; each
  question is answered directly beneath it in the same paragraph (**error** if
  unanswered — `Not answered — <why>` counts); each answer should cite its
  evidence (Fig/Table/link — warning otherwise).
- **The answer sheet leads** — Setup/What-was-run before Questions warns
  (paper-order).
- **Reproduce** carries exact commands (a fenced `bash` block).
- **Figures** referenced in the report must exist on disk.
- **Provenance footer** — an italic line naming Branch / Model / Artifacts / Code.

Hard requirements are **errors**; heuristic conventions (thesis-as-sentence,
evidence pointers, answers-first ordering, provenance footer,
Evidence-leads-with-figure) are **warnings**. `level = "warn"` makes warnings
fail too.

## Install

```bash
pip install git+https://github.com/dtch1997/reportly
```

## CLI

```bash
reportly new my-experiment          # scaffold reports/my-experiment.md from the template
reportly new my-experiment \
  -q "Does depth change durability?" \
  -q "Is the effect scale-dependent?"   # pre-populate the answer sheet from the spec
reportly lint reports/              # enforce the standard (exit non-zero on failure)
reportly lint reports/ --show-warnings
reportly build reports/             # render *.md -> *.html + index.html (in place)
reportly build reports/ --out site/
```

## Library

```python
import reportly

reportly.scaffold("my-experiment", "reports",
                  questions=["Does depth change durability?"])

cfg = reportly.load_config("reports")
issues = reportly.lint_path("reports", cfg)      # {Path: [Issue, ...]}
for path, found in issues.items():
    for i in found:
        print(i.format())

reportly.build("reports", config=cfg)
```

## Config (`reportly.toml`)

Placed at or above the reports directory:

```toml
reports_dir = "reports"
required    = ["questions", "result", "setup", "discussion", "next_steps", "reproduce"]
vibe_values = ["positive", "negative", "mixed"]
level       = "error"          # or "warn" — whether warnings also fail
disable     = ["result_figure"]

[sections]                     # extend/override the alias map (kind = [aliases])
setup = ["setup", "method", "protocol", "what was run"]
```

## CI / Pages

Lint in CI and build the site for GitHub Pages (HTML is regenerated, so commit
only `.md` + `figs/`):

```yaml
- run: pip install git+https://github.com/dtch1997/reportly
- run: reportly lint reports/
- run: reportly build reports/
```
