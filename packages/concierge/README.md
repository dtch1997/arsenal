# concierge

A worker pool over headless Claude sessions: **durable tasks in, gated
artifacts out**. Submit a spec with an externally-checkable completion gate;
a stateless reconciler dispatches it to a resumable `claude -p` worker,
retries with feedback when the gate fails, parks it `blocked` when the worker
asks a question, and notifies you on every terminal transition.

See [SPEC.md](SPEC.md) for the design (primitives, state machine, verbs).

## Quickstart

```bash
pip install -e .
export CONCIERGE_HOME=~/concierge-home

echo "Write a report on X into report.md" > spec.md
concierge submit spec.md --repo <git-url> --gate file_exists:report.md --budget-usd 20
concierge serve            # the reconciler daemon (run once, anywhere durable)

concierge status           # table of all tasks
concierge await <id>       # block until done/failed; exit 0 iff gate passed
concierge msg <id> "..."   # answer a blocked worker / redirect
concierge logs <id>        # rendered agent event stream
```

Both interaction modes are first-class: `await` for request/response
scripting, and pluggable notifications (stdout/Slack) for fire-and-forget.

## Status

Prototype (v0). Built-in `ClaudeCliRuntime` over `claude -p --output-format
stream-json` + `--resume`; the `Runtime` seam is deliberately tiny so other
runtimes (flightdeck, shepherd) can back it later.
