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
from concierge import Pool, FileExists, ShellOk

async def main():
    pool = Pool("~/concierge-home")

    tid = pool.submit(
        "Write a report on X into report.md",
        repo="git@github.com:you/proj.git",
        gate=FileExists("report.md") & ShellOk("reportly lint report.md"),
        budget_usd=20,
    )

    task = await pool.wait(tid)            # status == "done" iff the gate passed
    if task["status"] == "blocked":
        pool.msg(tid, "answer to the worker's question")
        task = await pool.wait(tid)
    print(pool.transcript(tid))

asyncio.run(main())
```

Sweeps are ordinary asyncio fan-in:

```python
tids = [pool.submit(spec, repo=..., gate=ShellOk("pytest -q")) for spec in variants]
results = await pool.wait_all(tids)
```

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
