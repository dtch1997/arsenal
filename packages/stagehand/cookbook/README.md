# cookbook

Recipes for doing each kind of experiment step **reliably**. Every step type
decomposes the same way:

| | | stagehand primitive |
|---|---|---|
| **body** | the work | `do` / `map` / `agent` |
| **criterion** | did it actually succeed? | `filter` / `with_retry(check=…)` + [`stagehand.checks`](../src/stagehand/checks.py) |
| **reliability** | what to do about the criterion | `with_retry`, gate-and-skip, persist-a-pointer, skip-if-done |

The recipes are **seam-based**: the tool-specific bits (compute backend, storage
sink, pointer location) are small functions you swap for your real stack — the
shape stays the same. Each runs with fakes so you can execute it anywhere.

## Recipes

- **[`train_and_persist.py`](train_and_persist.py)** — run a training/eval job →
  validate (`exit_ok & produced & finite`) → persist the artifact → record a
  pointer; idempotent (skips cells whose pointer already exists). Run it twice.

## Planned

- `code_with_agent.py` — an `agent(isolation="worktree")` implements/edits code,
  `with_retry` feeds `tests_pass` failures back until green; returns the diff.
- `figures_and_report.py` — render figures → QA-gate each (a judge `agent`) →
  `reduce` into a report → lint.

## The checks kernel

[`stagehand.checks`](../src/stagehand/checks.py) is the reusable part — small
predicates returning a `(ok, issues)` result that composes with `&` / `|` / `~`:

```python
from stagehand.checks import produced, finite, exit_ok
healthy = lambda r: exit_ok(r["exit"]) & produced(r["ckpt"]) & finite(r["loss"])
good = flow.filter("gate", trained, healthy)
```
