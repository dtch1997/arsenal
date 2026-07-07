# concierge

A worker pool over headless Claude sessions: **durable tasks in, gated
artifacts out**. Submit a spec with an externally-checkable completion gate;
a stateless reconciler dispatches it to a resumable `claude -p` worker,
retries with feedback when the gate fails, parks it `blocked` when the worker
asks a question, and notifies you on every terminal transition.

An asyncio-native **library** (in the spirit of bellhop), not a CLI. See
[SPEC.md](SPEC.md) for the design: primitives, state machine, verbs.

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

Drop a `HOUSE_RULES.md` in your `CONCIERGE_HOME` and every worker gets it
appended to its system prompt — pool-level conventions (artifact paths,
tooling norms, report standards) that a fresh workspace clone can't carry.
See [HOUSE_RULES.example.md](HOUSE_RULES.example.md).

Run the reconciler somewhere durable (it's stateless — kill and restart
freely):

```bash
python -m concierge serve          # or: await pool.serve() inside your own loop
```

`python -m concierge` has exactly two subcommands (`serve`, `msg`) — the two
things that must be shell-reachable. Everything else is the Python API:
`submit / wait / wait_all / msg / tasks / get / transcript / cancel / remove`.

## Status

Prototype (v0.2). Workers run on `AgentSdkRuntime`: each task gets a
detached `python -m concierge.worker` process running a Claude Agent SDK
session — the daemon never hosts sessions, so it can die and restart
without killing workers. Blocked-signaling is an in-process
`signal_blocked` tool; `access="readonly"` tasks get a read-only tool
allowlist. The `Runtime` seam is deliberately tiny so other runtimes
(flightdeck, shepherd) can back it later.
