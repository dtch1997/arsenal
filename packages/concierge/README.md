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
from concierge import Pool

async def main():
    pool = Pool("~/concierge-home")

    tid = pool.submit(
        "Write a report on X into report.md",
        repo="git@github.com:you/proj.git",
        gate="file_exists:report.md",
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
tids = [pool.submit(spec, repo=..., gate="shell_ok:pytest -q") for spec in variants]
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

Prototype (v0). Built-in `ClaudeCliRuntime` over `claude -p --output-format
stream-json` + `--resume`; the `Runtime` seam is deliberately tiny so other
runtimes (flightdeck, shepherd) can back it later.
