# cookbook

Recipes for doing experiment work **reliably**. Abstractly there are only **two
kinds of step**, and both are "produce → validate → persist a versioned artifact":

| | **(i) implementation** | **(ii) run** |
|---|---|---|
| **body** | an agent builds a feature | execute the code → artifact(s) |
| **artifact** | a code change (a diff) | data files: checkpoint / metrics / figure / report |
| **criterion** | a **review** approves it (tests + a reviewer) | the artifact is valid (`finite` / `valid_image` / `json_has`) |
| **reliability** | `with_retry`, review findings fed back, ≤ N times | idempotent skip-if-pointer; retry transient infra |
| **persist** | commit + open a **PR** (pointer = PR URL) | upload + record a **pointer** |

Reports and plots aren't a third kind — they're just *run steps* whose artifact is a
figure or markdown. The two compose into the experiment loop: **implement** (change
the code) → **run** (use the code to produce artifacts).

Every recipe is **seam-based** — the tool-specific bits (compute backend, storage
sink, coding agent, review, `gh`) are small functions you swap for your real stack;
the shape stays the same. Each runs with fakes so you can execute it anywhere.

## Recipes

- **[`run_step.py`](run_step.py)** — the run step, generalized: `run_and_persist(job,
  check, …)` runs a job → gates on `exit_ok & <your artifact check>` → persists the
  artifact + records a pointer; idempotent. One recipe, shown as both a *training*
  run (checked with `finite`) and a *plotting* run (checked with `valid_image`).
- **[`implementation_step.py`](implementation_step.py)** — the implementation step:
  an agent builds a feature, a **review** gates it, it **retries with the review's
  findings** up to N times, and the approved change is **PR'd**.

## The checks kernel

[`stagehand.checks`](../src/stagehand/checks.py) is the reusable part — small
predicates returning a `(ok, issues)` result that composes with `&` / `|` / `~`:

```python
from stagehand.checks import produced, finite, exit_ok, tests_pass
run_ok    = lambda r: exit_ok(r["exit"]) & produced(r["ckpt"]) & finite(r["loss"])
review_ok = lambda wt, diff: tests_pass(cwd=wt) & code_reviewer(diff)   # an impl-step review
```
