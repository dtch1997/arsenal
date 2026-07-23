# Usage patterns

How concierge is meant to be used — the decision rules and the canonical
shapes. Mechanics (states, config, safety hooks) live in the
[README](README.md); design rationale in [SPEC.md](SPEC.md).

## When to reach for concierge (and when not)

Concierge is for **deliverables**: work whose output is an artifact with an
externally checkable definition of done, which should exist even if the
session that requested it dies.

- *Information you need now to keep reasoning* → an in-session subagent, not
  concierge. Nothing durable should outlive the question.
- *A multi-step pipeline inside one experiment* (gen → train → eval, retries,
  fan-out over seeds) → **one** concierge task that uses your DAG tool
  (stagehand) internally. Pipeline stages are not pool tasks; a pool task is
  a unit someone could pick up cold from its spec.
- *A deliverable with a checkable done* (a report that lints, a PR that
  opens, results with ≥ N rows) → concierge.

The corollary: if you can't write the gate, the task isn't specified yet.
Sharpen the spec until "done" is a predicate a machine can check.

## The typed async function call

The default shape. A worker is an async function: the spec is the body, the
`output` schema types the return value, the gate types the side effects.

```python
@dataclass
class Findings:
    headline: str
    effect_size: float

result = await pool.run(
    "Run the ablation in specs/ablation.md; write report.md with the finding as H1.",
    repo="git@github.com:you/proj.git",
    gate=FileExists("report.md") & ShellOk("reportly lint report.md"),
    output=Findings,
    budget_usd=20,
)
```

Use when you have one well-specified deliverable and a caller that can
`await` it. `TaskFailed` carries the full task record for forensics.

## Handles, sweeps, and joining without polling

When dispatching several tasks, or when the caller might not live to see the
end, prefer handles:

```python
tids = [pool.submit(spec, repo=..., gate=ShellOk("pytest -q")) for spec in variants]
results = await pool.wait_all(tids)          # ordinary asyncio fan-in
```

If the submitting session is an agent harness, don't poll from the main
loop: background a tiny awaiter (`await pool.wait(tid)` then exit) and act on
its completion notification. The task is durable either way — a lost
submitter loses nothing.

## Gate design: results, not artifacts

The gate is the contract, and the pool checks it externally — never the
worker's self-report. Two rules of thumb:

- **Gate on results, not their containers.** `PrOpen()` alone lets a worker
  settle with a placeholder report while the real experiment still runs
  somewhere. Compose in a results assertion:

  ```python
  gate = PrOpen() & ShellOk("test $(wc -l < results.jsonl) -ge 200")
  ```

- **Prefer gates that already exist as tooling** — `reportly lint`, `pytest
  -q`, a row-count — over bespoke verification scripts the worker could
  game or break.

`Always()` (the default) is for genuinely gate-free chores; if the task
matters, it has a gate.

## Conversing with a task

Three verbs, three moments:

- **It asked a question** (`blocked`): `pool.msg(tid, "use seed 7")` — the
  same session resumes with your answer.
- **You want to redirect it mid-flight**: `pool.msg` also works on a
  `running`/`waiting` task; the message is delivered at the next resume.
- **It's finished and you have a follow-up**: `pool.ask(tid, "which seed was
  the outlier?")` rehydrates the settled session — full memory, no status
  change — and returns a (optionally typed) answer.

## Long external jobs: park, don't babysit

When a worker's deliverable depends on a job running *outside* it (a pod
pipeline, a training run), the worker neither waits in-session nor ships
placeholders — it calls `signal_waiting` with a cheap shell probe and stops.
The daemon polls the probe and resumes the same session to finish honestly.
This is a worker-side pattern, but it shapes how you write specs: tell the
task where the external job's completion will be observable (a GCS marker, a
log line), so it has something to probe.

A `waiting` task holds no worker slot and burns no attempt.

## Trees and leaves: delegation

For a task that splits into **independent, parallelizable** subtasks, the
worker can call up new workers within its own pool via the `delegate` tool.
This is queue-insertion, not pool-creation: children are ordinary tasks in
the same home, behind the same concurrency cap — when the pool is full, they
queue.

The choreography, from the parent's side:

1. Decompose. Write each child's spec the way you'd want to receive it —
   ambiguity collapsed, inputs/outputs explicit. The child sees nothing of
   your session.
2. `delegate(title=..., spec=..., gate=..., model=...)` once per child.
   Fully-specified mechanical leaves can take a cheaper `model`; children
   inherit yours by default. Child budgets are carved from the parent's
   remaining envelope, and depth is capped (`max_depth`, default 2).
3. Park on the probe the tool returns (`signal_waiting`), releasing your
   slot to your children. **Never wait in-session for children.**
4. Wake with every child's outcome — failures included. You are the
   recovery mechanism: retry with a sharper spec, absorb the work, or
   `signal_blocked`. Your own gate is still your definition of done.

Children that build on the parent's work: the parent commits **and pushes**
its branch, delegates with `base=<its branch>`, and merges child branches
before settling — only the parent's branch PRs to main.

**Anti-pattern:** a sequential chain (A then B then C) as parent-and-
children. Delegation buys parallelism and context isolation; sequencing
belongs inside one task's DAG.

## Model economics

`model=` (on `submit` and `delegate`) is the cost dial. The moments that need
frontier intelligence are few — decomposition, design trade-offs, judgment
calls; execution of a fully-collapsed spec usually doesn't. Run planners and
judgment-heavy tasks on the default model; delegate mechanical leaves (apply
this refactor, run this eval matrix, port this test file) down-tier. If a
leaf keeps failing its gate on a cheap model, that's usually a spec problem
before it's a model problem.

## Anti-patterns, collected

- **Underspecified specs** — "investigate X and do the right thing" burns
  attempts on clarification round-trips. If you can't write the gate, keep
  sharpening.
- **Gating on containers** (`PrOpen()` alone) when the deliverable is
  results.
- **Pipelines as delegation chains** — sequential stages belong in one
  task's DAG tool, not sibling pool tasks.
- **Polling from the submitting loop** — background an awaiter and act on
  its notification; the task record is durable regardless.
- **In-session waiting** — on external jobs (`signal_waiting` instead) or on
  children (park on the children-probe instead). Both hold resources hostage
  to a clock.
