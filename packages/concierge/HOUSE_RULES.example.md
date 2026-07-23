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

## Background tasks
- Run the real long command directly with run_in_background — no
  nohup/disown/setsid, no trailing `&`. Detaching makes the harness track a
  launcher that exits instantly, orphaning the job. (A workspace guard hook
  also blocks these, but don't rely on it.)
- Never ship placeholder results to satisfy a gate while the real work is still
  computing.
- If your deliverable depends on a long-running job running OUTSIDE this worker
  (a pod pipeline, a training run), do not wait in-session for more than a few
  minutes — the session output timeout will kill the attempt. Call the
  `signal_waiting` tool with a cheap shell probe that exits 0 when the job is
  done (for `rclone lsf` and friends that exit 0 on a missing object, test the
  OUTPUT is non-empty: `test -n "$(rclone lsf gcs:.../DONE)"`), then stop. The
  daemon polls it and resumes this same session to finish — no attempt burned.

## Delegation (trees and leaves)
- You may call up new workers within the pool via the `delegate` tool — but
  only for subtasks that are *independent and parallelizable*, each with an
  externally checkable definition of done. A sequential pipeline is ONE task
  orchestrated with your DAG tool, not a chain of children.
- Write each child's spec the way you'd want to receive it: ambiguity
  collapsed, inputs/outputs explicit, done-condition stated. A child sees
  nothing of your session. Fully-specified mechanical leaves can take a
  cheaper `model`.
- Children that build on your work: commit AND push your branch first, then
  delegate with `base=<your branch>`; you merge their branches before you
  settle — only your branch PRs to main.
- After delegating all children, park via `signal_waiting` on the probe the
  delegate tool returns — never wait in-session for children (you'd hold a
  pool slot they need). You will be resumed with every child's outcome,
  including failures: you are the recovery mechanism (retry with a sharper
  spec, absorb the work, or signal_blocked). Your own gate is still your
  definition of done.

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
