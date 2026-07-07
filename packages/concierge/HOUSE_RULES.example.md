# HOUSE_RULES.md — example

Put a `HOUSE_RULES.md` in your `CONCIERGE_HOME` and every worker gets it
appended to its system prompt. This is where pool-level conventions live —
the things a fresh workspace clone can't tell a worker: where artifacts go,
which tools are house standards, what a report must look like. Keep it
short; gates enforce, rules orient.

Example:

```markdown
## Artifacts
- Large artifacts (checkpoints, datasets, eval dumps) go to
  <your object store path>/<task-or-experiment-slug>/.
  Commit *pointers* (paths/URLs) to the repo, never the bytes.

## Compute
- GPU/heavy jobs run on ephemeral cloud machines via <your dispatch lib>;
  never hand-provision or SSH by hand.

## Reports
- Every experiment produces a report.md: finding as the H1, then TL;DR,
  Setup, Result, Reproduce. Include exact commands and seeds.

## Git
- Work only on your task branch. Small commits, clear messages.
  Never force-push. Open PRs against main.

## Judgment
- If a decision is irreversible or will spend real money and the spec
  doesn't settle it, ask via signal_blocked instead of guessing.
- Never commit secrets; credentials come from the environment only.
```
