# concierge

A worker pool over headless Claude sessions: **durable tasks in, gated
artifacts out**. Submit a spec with an externally-checkable completion gate;
a stateless reconciler dispatches it to a resumable `claude -p` worker,
retries with feedback when the gate fails, parks it `blocked` when the worker
asks a question, and notifies you on every terminal transition.

An asyncio-native **library** (in the spirit of bellhop), not a CLI. See
[SPEC.md](SPEC.md) for the design (primitives, state machine, verbs) and
[PATTERNS.md](PATTERNS.md) for intended usage patterns — when to reach for
concierge, gate design, delegation, model economics.

## Quickstart

```python
import asyncio
from dataclasses import dataclass
from concierge import Pool, FileExists, ShellOk

@dataclass
class Findings:
    headline: str
    effect_size: float

async def main():
    pool = Pool("~/concierge-home")

    # a worker is a typed async function call: the output schema types the
    # returned data, the gate types the side effects. Raises TaskFailed
    # (with the task record attached) unless the task ends done.
    result = await pool.run(
        "Run the ablation described in specs/ablation.md; write report.md",
        repo="git@github.com:you/proj.git",
        gate=FileExists("report.md") & ShellOk("reportly lint report.md"),
        output=Findings,
        budget_usd=20,
    )
    print(result.headline, result.effect_size)

    # rehydrate the same session later for follow-ups (full memory)
    tid = pool.tasks()[-1]["id"]
    print(await pool.ask(tid, "which seed was the outlier?"))

asyncio.run(main())
```

Prefer handles over calls when dispatching many at once: `tid = pool.submit(...)`,
`await pool.wait(tid)` / `await pool.wait_all(tids)`, `pool.msg(tid, "answer")`
when a worker blocks on a question, `pool.transcript(tid)` to read the session.

Sweeps are ordinary asyncio fan-in:

```python
tids = [pool.submit(spec, repo=..., gate=ShellOk("pytest -q")) for spec in variants]
results = await pool.wait_all(tids)
```

### Task dependencies (`after=`) — parallel, then join

`pool.submit(spec, after=[tid, ...])` expresses *"run this after those finish."*
The classic shape is A/B/C in parallel, then D once all three are `done`:

```python
a = pool.submit(spec_a, repo=..., gate=ShellOk("pytest -q"))
b = pool.submit(spec_b, repo=..., gate=ShellOk("pytest -q"))
c = pool.submit(spec_c, repo=..., gate=ShellOk("pytest -q"))
d = pool.submit(polish_spec, after=[a, b, c])   # queued but held until A,B,C are done
```

Every id in `after` must already exist (submit raises `ValueError` otherwise);
because a dependency must predate its dependent, cycles are impossible by
construction — no cycle check needed. The dependent sits in the **`held`**
status — it consumes no worker slot and no `concurrency` seat — until the
reconciler sees every dependency reach `done`, at which point it releases to
`queued` and dispatches normally. If any dependency ends **not-done**
(`failed`/`cancelled`, or its record vanishes), the held task **fails fast**
with `dependency <tid> ended <status>` rather than waiting forever; that is not
a gate failure, so it burns no strike and writes no `gate_result`. The `after`
edges are surfaced on the task record (and `pool.tasks()`) so desk/dashboards
can render the DAG, and `held` shows `status_detail: "held: waiting on <tids>"`
narrowed to the still-unmet deps. `wait`/`wait_all` work unchanged on dependent
tasks. The whole join lives in the reconciler and is re-derived from records on
disk every tick — durable across daemon restarts, unlike a submitter-side
`wait_all → submit` driver whose join dies with its process.

This is deliberately **join-only** ordering (a DAG of peer tasks submitted
top-down), not a workflow engine: fan-out/retry *within* a task stays with
stagehand, and delegation stays the tree-shaped mechanism for a worker
decomposing its own task.

Drop a `HOUSE_RULES.md` in your `CONCIERGE_HOME` and every worker gets it
appended to its system prompt — pool-level conventions (artifact paths,
tooling norms, report standards) that a fresh workspace clone can't carry.
See [HOUSE_RULES.example.md](HOUSE_RULES.example.md).

### Workspace safety & env

Every workspace is provisioned with a `PreToolUse(Bash)` guard hook that blocks
the background-task anti-patterns which silently drop the harness completion
signal — `nohup`/`disown`/`setsid`/trailing-`&` detaches inside
`run_in_background` commands, and self-matching `pgrep -f` watcher loops. The
hook is copied into the workspace's `.claude/` and merged into its
`.claude/settings.json` (never clobbering a cloned repo's own settings); the
added files are kept out of worker PRs via `.git/info/exclude` and
`skip-worktree`.

Set `env_file` (config.yaml or a `Pool(...)` kwarg) to pre-seed every worker's
environment from a dotenv file — defaults to `~/.env` if it exists, set to
`null` to disable. Values override inherited `os.environ`; the concierge-set
vars (`CONCIERGE_HOME`, `CONCIERGE_TASK_ID`, `PYTHONPATH`) always win last. This
saves every worker from rediscovering API keys with `set -a; . ~/.env`.

When a worker exits, the evaluated gate outcome is stored as structured data on
the task record: `task["gate_result"] = {"passed", "detail", "checked_at"}`.

### Worker lifecycle & the `waiting` state

A task moves `queued → running → done`, with three ways to hand control back
without failing:

- **`held`** — the task was submitted with `after=[…]` and at least one
  dependency isn't `done` yet. It holds no worker slot and burns no strike; the
  reconciler releases it to `queued` when all deps are done, or fails it fast if
  one ends not-done. See *Task dependencies* above.
- **`blocked`** — the worker called `signal_blocked` with a question; a user
  message resumes it.
- **`waiting`** — the worker called `signal_waiting`: its real work is a
  long-running job *outside* the worker (a bellhop pod pipeline, a training
  run). Rather than ship a placeholder or wait in-session (both of which the
  SDK's output timeout kills), the worker declares a cheap shell probe
  (`until_shell`, exit 0 = done) plus a human `note`, writes an atomic sidecar
  `tasks/<id>.wait.json`, and stops. The daemon polls the probe in the
  workspace and resumes the *same* session when it fires or times out.
- **gate fail** — the worker exited but the gate is unmet; it resumes with
  feedback.

Only gate-checked failures count toward `max_attempts` (tracked on
`task["gate_failures"]`); resuming from `blocked` or `waiting` never burns a
strike. Two config keys tune waiting: **`wait_poll_seconds`** (default 60) —
how often the reconciler evaluates a waiting task's probe — and
**`wait_timeout_minutes`** (default 720) — the fallback deadline a
`signal_waiting` call inherits when it omits its own `timeout_minutes`. A
`waiting` task holds no worker slot, so it doesn't count against `concurrency`.

Run the reconciler somewhere durable (it's stateless — kill and restart
freely):

```bash
python -m concierge serve          # or: await pool.serve() inside your own loop
```

`python -m concierge` has exactly three subcommands (`serve`, `msg`,
`probe-children`) — the things that must be shell-reachable. Everything else
is the Python API:
`submit / wait / wait_all / msg / tasks / get / transcript / cancel / remove`.

## Status

Prototype (v0.2). Workers run on `AgentSdkRuntime`: each task gets a
detached `python -m concierge.worker` process running a Claude Agent SDK
session — the daemon never hosts sessions, so it can die and restart
without killing workers. Blocked-signaling is an in-process
`signal_blocked` tool; `access="readonly"` tasks get a read-only tool
allowlist. The `Runtime` seam is deliberately tiny so other runtimes
(flightdeck, shepherd) can back it later.
