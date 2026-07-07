# concierge — design spec (v0)

**TL;DR.** An asyncio-native library (+ reconciler daemon) that turns
headless Claude sessions into a worker
pool you can make requests to. Four primitives — **Task**, **Worker**,
**Message**, **Pool** — with everything else (portals, bots, cron) demoted to
clients of the same small verb set. Runs on a purpose-built ~150-line runtime
over `claude -p` (behind a swappable `Runtime` seam); all state is dumb files;
both request/response (`await`) and fire-and-forget (notifications) are
first-class from day one.

Named **concierge**: takes requests, dispatches staff; pairs with bellhop.
Lives at `dtch1997/concierge`.

---

## Design stance

1. **The primitives are the trust boundary, not the interface.** CLI, HTTP,
   a board UI, a Slack bot — all clients of the same four primitives. We can
   swap interfaces freely without re-litigating the core.
2. **Completion is externally checkable, never self-reported.** A task
   without a gate is a wish, not a task.
3. **State is dumb files; the daemon is a stateless reconciler.** The pool
   process can die and restart with zero lost state. No database, no
   in-memory queue.
4. **Agents don't need GPUs; their runs do.** Workers are cheap subprocesses
   on a CPU host; GPU work is dispatched from *inside* tasks via bellhop.
   Pool capacity is API budget, not machines.

## Primitives

### 1. Task = spec + workspace + gate + budget

The unit of request. One JSON record + one Markdown spec per task.

```jsonc
// $CONCIERGE_HOME/tasks/t-0042.json
{
  "id": "t-0042",
  "title": "Sweep LoRA rank on backdoor durability",
  "spec": "specs/t-0042.md",              // prompt body (Markdown)
  "workspace": {
    "repo": "git@github.com:ArcadiaImpact/foo.git",
    "base": "main",
    "branch": "pool/t-0042",              // one branch+worktree per task
    "access": "readwrite"                 // or "readonly" (see shepherd note)
  },
  "gate":   { "kind": "shell_ok", "cmd": "reportly lint report.md", "timeout": 600.0 },
  "budget": { "usd": 20, "wall_minutes": 240 },
  "priority": 0,                          // higher = sooner; FIFO within priority
  "status": "queued",                     // see state machine
  "attempts": [                           // one entry per session (spawn or resume)
    { "session_id": null, "cost_usd": 0, "result": null, "log": "logs/t-0042/attempt-1/" }
  ],
  "max_attempts": 3,                      // strikes before failed
  "notify": ["slack"],                    // channels pinged on done/blocked/failed
  "links": { "pr": null, "report": null, "dashboard": null },
  "created": "2026-07-07T…", "updated": "2026-07-07T…"
}
```

Gates are **Gate objects** (`concierge.gates`) — declarative, serializable
predicates, not closures, because the evaluating process (the reconciler,
possibly restarted or remote) is not the submitting one. The contract:
`check(ctx) → Verdict(passed, detail, links)` (evidence + PR-link stamping,
not a bare bool), `describe()` (rendered into the worker's preamble),
`to_json()`/`Gate.from_json()` (registry by `kind`; custom gates = subclass
in a module the daemon imports), and composition via `&`/`|` (`AllOf`/`AnyOf`).
Built-ins (semantics borrowed from flightdeck): `ShellOk(cmd)` (run in the
task workspace), `FileExists(path)`, `PrOpen(branch)`, `PrMerged(branch)`,
`Always()` — e.g. `FileExists("report.md") & ShellOk("reportly lint report.md")`.
Evaluated by the *pool* after worker exit.

Budget lives on the task (part of the request's contract), enforced by the
pool: cost from the session event stream, wall-clock from spawn time. A
pool-level daily cap is a config knob on top, not a substitute.

### 2. Worker = a resumable, observable agent session

The layer we actually trust is one *below* any wrapper: `claude -p` +
`--output-format stream-json` + `--resume <session_id>`, plus OS processes
and log files — Anthropic's contract and the kernel's, not ours. A worker is
**addressable state**, not a fire-and-forget process — crashes resume,
blocked workers can be spoken to, retries carry context ("your gate failed
with: …") instead of starting cold.

The pool defines a minimal **`Runtime` seam** over that layer —
`spawn(task, prompt) → attempt{pid, log}`, `alive(pid)`,
`observe(log) → {session_id, cost, result}`, `kill(pid)`; resume is just
spawn with `--resume` — and ships a purpose-built `ClaudeCliRuntime`
(~150 lines).

**Why not flightdeck as the runtime** (considered, rejected):
`AgentRun.go()` is a *blocking* call that owns lifecycle policy — gates,
alerts, retry — exactly the policy the reconciler owns here, so we'd fight
it or gut it. And a restart-surviving reconciler must **re-attach** to
worker processes it didn't spawn (pid + logs on disk), which an in-process
blocking wrapper can't offer. flightdeck's event-stream parsing and gate
semantics are borrowed; flightdeck and shepherd both remain candidate
backends behind the seam.

Worker self-reports are advisory only. The pool decides done/failed by
running the gate itself.

### 3. Message = an async note to/from a task

Per-task mailbox: `$CONCIERGE_HOME/mailbox/t-0042.jsonl`, entries
`{"from": "user"|"worker", "text": …, "ts": …, "via": "cli"|"github"|…}`.

The mailbox is the **control channel** — the single stream the reconciler
trusts for state transitions. **Transports are pluggable** and all feed it:

- **Mailbox (built-in):** short messages. Worker → user (blocked): the task
  prompt instructs the worker: *if you cannot proceed without input, run
  `python -m concierge msg <id> --from worker "<question>"` and exit.* Pool
  sees exit + unanswered worker message + gate unmet → status `blocked`,
  notification fires. User → worker: `pool.msg(tid, "answer")` → pool resumes
  the session with the message. Also works for mid-flight redirects ("also
  try X") — queued and delivered on next resume.
- **GitHub (bridge, v1.5):** once a task has an anchor (`links.pr`, or an
  originating issue/discussion), substantive conversation lives *there*,
  next to the artifact it's about — PR review comments especially. A small
  poller (`gh api`, cursor per task) mirrors new comments from you into the
  mailbox (→ resume, worker addresses them and replies via `gh pr comment`).
  With a `pr_merged` gate this makes the review conversation literally the
  completion path: the worker iterates on review comments until merge.

Division of labor: **mailbox for state** (blocked/resume/redirect — short,
structured, low-latency), **GitHub for content** (review-quality discussion,
durable and anchored to the diff). GitHub is never the queue and never
required — a task that dies before opening a PR has no anchor, and the
task tracker stays in-repo (cairn convention), not GH issues. Chat is the
degenerate case of all this; the default is async and logged.

### 4. Pool = a stateless reconciler

Each tick (a few seconds), scan `tasks/` and reconcile:

| Observation | Action |
|---|---|
| `queued`, free slot | create worktree, spawn `AgentRun`, → `running` |
| `running`, process exited, gate **passes** | → `done`, stamp links, notify |
| `running`, exited, gate fails, unanswered worker message | → `blocked`, notify |
| `running`, exited, gate fails, attempts < max | resume session with gate feedback (new attempt) |
| `running`, exited, gate fails, attempts ≥ max | → `failed`, notify |
| `running`, budget (usd/wall) exceeded | kill, → `failed(budget)`, notify |
| `blocked`, new user message | resume with message, → `running` |

State machine:

```
queued ──▶ running ──▶ done
              │ ▲          
              ▼ │ (user msg)
           blocked
              │
              ▼ (strikes / budget / cancel)
           failed
```

Concurrency cap (default ~4) is the only scheduling sophistication in v1.
No triage, no ranking, no backlog intelligence — `priority` int + FIFO.

## Verbs (asyncio-native Python API; HTTP mirrors it)

concierge is a **library**, not a CLI. Fast file-ops are plain methods; the
blocking verbs are coroutines.

```python
from concierge import Pool, ShellOk
pool = Pool("~/concierge-home")     # a handle on one CONCIERGE_HOME

tid  = pool.submit(spec, repo=…, gate=ShellOk("pytest -q"),
                   budget_usd=20, priority=1)      # → task id
task = await pool.wait(tid, timeout=4*3600)        # → final record; "done" iff gate passed
done = await pool.wait_all(tids)                   # gather-style fan-in for sweeps
pool.msg(tid, "answer")                            # answer a blocked worker / redirect
pool.tasks(); pool.get(tid)                        # records incl. status, cost, links
print(pool.transcript(tid))                        # human-rendered agent event stream
pool.cancel(tid)
await pool.serve()                                 # the reconciler daemon
```

Two shell shims exist (`python -m concierge msg|serve`) because two things
must be reachable from a shell: the worker's blocked-signal, and launching
the daemon under tmux/systemd.

**Both verbs, day one:**
- *Request/response:* `await pool.wait(tid)` makes a task feel like an async
  function call; `wait_all` composes sweeps with ordinary asyncio fan-in.
- *Fire-and-forget:* every terminal transition and every `blocked` fires the
  pluggable notifier (Slack webhook / stdout), so "dispatch 10, check in
  tomorrow" needs no polling.

HTTP (v1.5, after the library is proven): FastAPI mirroring the verbs 1:1,
bearer token, `marquee` for the public URL, and a `Pool`-compatible HTTP
client class. This is what makes "spin up a pool via bellhop and make
requests to it" literal — bellhop checks the daemon into a cheap CPU pod,
marquee exposes it, `Pool(host=<url>)` from anywhere.

## On-disk layout

```
concierge-home/
  config.yaml            # concurrency, daily cap, notifier, defaults
  tasks/<id>.json        # control state (the reconciler's ground truth)
  specs/<id>.md          # prompt bodies
  mailbox/<id>.jsonl     # messages
  logs/<id>/attempt-N/   # agent.jsonl + agent.err per session
  workspaces/<id>/       # one clone/worktree per task
```

## Relationship to existing tools

- **flightdeck** — *not* a dependency (see the Worker primitive): its
  blocking `AgentRun.go()` owns policy the reconciler owns here, and it
  can't re-attach across daemon restarts. Event-parsing + gate semantics
  borrowed; remains a candidate backend behind the `Runtime` seam.
- **bellhop** — two roles: (a) hosts the pool daemon itself on a cheap pod;
  (b) used *by workers inside tasks* for GPU runs. No coupling in pool code.
- **stagehand** — tasks that are themselves DAGs can serve a stagehand
  dashboard; the worker stamps its URL into `links.dashboard`.
- **foreman** — becomes (if revived) a pure client: a board UI over
  `concierge status` + `concierge submit`. Its dispatch stub is superseded by the
  reconciler.
- **shepherd** (github.com/shepherd-agents/shepherd) — an alternative
  *runtime* behind the same seam, not a pool. Two ideas adopted into this
  spec now: `workspace.access` (declare read-only vs read-write up front)
  and retained-outputs framing (nothing auto-applies; gates + PR review are
  our retention boundary). Full evaluation deferred — see Open questions.

## Non-goals (v1)

- Ranking / triage / backlog intelligence (deliberately cut with flywheel)
- Any UI
- Multi-host worker daemons (pull in later only if agents must live on GPU
  machines)
- Sandboxing beyond one-worktree-per-task (revisit via shepherd if needed)

## Open questions

1. ~~**Name.**~~ Resolved: `concierge`.
2. ~~**Repo residence.**~~ Resolved: spun out to `dtch1997/concierge`.
3. **Blocked-signaling mechanism.** Worker calls the `python -m concierge
   msg` shim (spec'd above) vs writing a sentinel file the reconciler picks
   up — the shim is cleaner but requires the worker env to import concierge
   (the runtime injects PYTHONPATH + CONCIERGE_HOME today).
4. **Shepherd as runtime.** Its jailed enforcement (Landlock) + fork/replay
   could give cheaper retries and real isolation, but it's alpha (v0.2.1)
   and single-run scoped. Evaluate once the pool works end-to-end on the
   built-in runtime.
5. **Agent SDK inside the worker process (leading candidate, v0.2).** The
   SDK runs the agent loop *in your process* — putting it in the daemon
   would kill workers on daemon death, the exact flaw that ruled out
   flightdeck. But spawning a thin detached wrapper per task
   (`python -m concierge.worker <id>`) that runs an SDK session *inside the
   worker process* keeps the pid+logs re-attach model and buys: typed
   messages instead of hand-parsed stream-json; the blocked-signal as an
   in-process custom tool (retiring the PYTHONPATH shim, open question 3);
   `workspace.access: readonly` enforced via allowed_tools/permission
   callbacks instead of being advisory; `setting_sources` isolation from
   the parent project's config (cleaner than `--strict-mcp-config`).
   Cost: a real dependency; one more process layer. Same session_id/resume
   semantics underneath, so the reconciler doesn't change.
