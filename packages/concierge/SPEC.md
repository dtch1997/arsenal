# concierge — design spec (v0)

**TL;DR.** A daemon + CLI that turns headless Claude sessions into a worker
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
  "gate":   { "kind": "shell_ok", "arg": "test -f report.md && reportly lint report.md" },
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

Gate kinds (semantics borrowed from flightdeck; evaluated by the *pool*
after worker exit):
`pr_open(branch)`, `pr_merged(branch)`, `file_exists(path)`,
`shell_ok(cmd)` (run in the task workspace), `always()`.

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

- **CLI (built-in):** short messages. Worker → user (blocked): the task
  prompt instructs the worker: *if you cannot proceed without input, run
  `concierge msg <id> --from worker "<question>"` and exit.* Pool sees exit +
  unanswered worker message + gate unmet → status `blocked`, notification
  fires. User → worker: `concierge msg <id> "answer"` → pool resumes the session
  with the message. Also works for mid-flight redirects ("also try X") —
  queued and delivered on next resume.
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

## Verbs (CLI = the API; HTTP mirrors it)

```
concierge submit spec.md --repo <url> [--gate shell_ok:"…"] [--budget-usd 20] \
                    [--priority 1] [--notify slack]        → prints task id
concierge await <id> [--timeout 4h]      # sync verb: blocks until terminal state;
                                    # exit 0 = done (gate passed), else nonzero
concierge status [<id>]                  # table / single-task detail incl. cost, links
concierge logs <id> [-f]                 # tail agent.jsonl (human-rendered)
concierge msg <id> "text"                # answer a blocked worker / redirect a running one
concierge cancel <id>
concierge serve [--tunnel]               # the daemon: reconciler + HTTP API (+ marquee)
```

**Both verbs, day one:**
- *Request/response:* `concierge await` makes a task feel like a function call —
  scriptable (`concierge submit … && pool await …`), composable into sweeps.
- *Fire-and-forget:* every terminal transition and every `blocked` fires the
  pluggable notifier (Slack webhook / stdout), so "dispatch 10, check in
  tomorrow" needs no polling.

HTTP (v1.5, after CLI is proven): FastAPI mirroring the verbs 1:1, bearer
token, `marquee` for the public URL. This is what makes "spin up a pool via
bellhop and make requests to it" literal — bellhop checks the daemon into a
cheap CPU pod, marquee exposes it, `concierge --host <url> submit …` from anywhere.

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
3. **Blocked-signaling mechanism.** Worker calls `concierge msg` CLI (spec'd
   above) vs writing a sentinel file the reconciler picks up — CLI is
   cleaner but requires the worker host to have pool on PATH and reach the
   daemon.
4. **Shepherd as runtime.** Its jailed enforcement (Landlock) + fork/replay
   could give cheaper retries and real isolation, but it's alpha (v0.2.1)
   and single-run scoped. Evaluate once the pool works end-to-end on the
   built-in runtime.
