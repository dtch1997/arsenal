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
  "output_schema": { "type": "object", "properties": { } },  // or null; types the return value
  "output": null,                         // structured output, stamped on done
  "result_text": null,                    // the session's final text, stamped on done
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

### 2. Worker = a durable handle on one agent session

Four verbs and a snapshot (`concierge.Worker` / `WorkerState`):

```python
Worker.spawn(home, task, prompt, cfg, resume=None) -> Worker   # start attempt N
Worker.attach(home, task)                          -> Worker   # rebuild from the record
worker.poll()                                      -> WorkerState
worker.kill()

WorkerState: alive (OS process view), ended (event-stream view — authoritative),
             session_id, cost_usd, error, text, output (structured)
  .running   = alive and not ended
  .lingering = alive and ended        # session over, process didn't exit: reap
```

The invariant that shapes the interface: **a Worker is constructible from
the task record alone** (`attach`). The daemon observing a worker is usually
not the daemon that spawned it (restarts), so identity is durable state —
pid + log dir — never an in-process handle. Same constraint as Gate:
observer ≠ spawner. `poll()` joins the two sources of truth (process table
+ event log) into one immutable snapshot; a worker is **addressable state**
— crashes resume via `session_id`, blocked workers can be spoken to, and
retries carry context ("your gate failed with: …") instead of starting cold.

The session runs in a thin detached wrapper (`python -m concierge.worker
<id> <attempt>`) hosting an Agent SDK session — *never in the daemon*. The
SDK buys: `signal_blocked` as an in-process custom tool;
`workspace.access: readonly` enforced via the tool allowlist;
`setting_sources=["project"]` (target repo's CLAUDE.md, not the spawner's
global config); `max_budget_usd` in-session. Pool-level conventions live in
`$CONCIERGE_HOME/HOUSE_RULES.md`, appended to every worker's system prompt
— the harness soul (artifact paths, tooling norms, report standards) that a
bare workspace clone wouldn't carry; gates enforce, rules orient.

**Daemon down ≠ orphaned workers.** A worker is one agent session, not a
loop, and it is *self-bounding*: USD via `max_budget_usd`, wall-clock via
an in-process timeout, and it exits deterministically the moment its
terminal result event is flushed — taking its process group with it, since
SDK transport cleanup is not trusted to terminate. Everything lands on
disk, so a daemon that was down while a worker finished simply `attach`es
on restart, runs the gate, and settles (verified e2e: dispatch by daemon
#1, worker completes with no daemon alive, daemon #2 settles it). The
reconciler's wall/reap checks are backstops, not the enforcement. One
subtlety: a dead worker stays a zombie of its spawning daemon until reaped,
so `alive` treats zombies as dead.

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
| `running`, exited, worker called `signal_waiting` (current-attempt sidecar) | consume sidecar, → `waiting` (no gate check, no strike), notify |
| `running`, exited, gate fails, unanswered worker message | → `blocked`, notify |
| `running`, exited, gate fails, gate_failures < max | resume session with gate feedback (new attempt) |
| `running`, exited, gate fails, gate_failures ≥ max | → `failed`, notify |
| `running`, budget (usd/wall) exceeded | kill, → `failed(budget)`, notify |
| `blocked`, new user message | resume with message, → `running` |
| `waiting`, wake probe exits 0 (throttled to `wait_poll_seconds`) | resume same session to finish, → `running` |
| `waiting`, elapsed > `timeout_minutes` | resume with a timeout note (normal attempt), → `running` |
| `waiting`, new user message | resume with message, → `running` |

`waiting` (issue #2) lets a worker park on a multi-hour job running *outside*
the worker (a pod-side pipeline) without shipping a placeholder or burning a
retry. The worker calls the `signal_waiting` tool with a cheap shell probe
(`until_shell`, exit 0 = done) and a human-readable `note`; it writes an atomic
sidecar `tasks/<id>.wait.json` (the daemon owns the record, so the worker never
mutates it) and stops. The reconciler polls the probe in the workspace at
`wait_poll_seconds` and resumes the *same* session when it fires or times out.
Strikes are counted on `gate_failures` (gate-checked attempts only), so
resuming from `blocked`/`waiting` never consumes an attempt; the `attempts`
list stays full history.

State machine:

```
                  ┌──── (user msg) ────┐
                  ▼                     │
queued ──▶ running ──▶ done          blocked
              │  ▲                      ▲
              │  └─ (probe/timeout/msg) │ (gate fail + worker question)
              │  └──── waiting ◀─(signal_waiting)
              ▼ (strikes / budget / cancel)
           failed

blocked and waiting resume back into running; neither burns a strike.
```

Concurrency cap (default ~4) is the only scheduling sophistication in v1.
No triage, no ranking, no backlog intelligence — `priority` int + FIFO.

## Verbs (asyncio-native Python API; HTTP mirrors it)

concierge is a **library**, not a CLI. Fast file-ops are plain methods; the
blocking verbs are coroutines.

```python
from concierge import Pool, ShellOk, TaskFailed
pool = Pool("~/concierge-home")     # a handle on one CONCIERGE_HOME

# the typed-function verb: a worker is a function-shaped call. Declare the
# output type (dataclass/TypedDict/JSON schema); the gate types the side
# effects. Raises TaskFailed (record on .task) unless the task ends done.
result = await pool.run(spec, repo=…, gate=ShellOk("pytest -q"),
                        output=ExperimentSummary, budget_usd=20)

# the handle verbs: dispatch now, observe/join later
tid  = pool.submit(spec, repo=…, gate=…, output=…)  # → task id
task = await pool.wait(tid, timeout=4*3600)        # → final record; "done" iff gate passed
done = await pool.wait_all(tids)                   # gather-style fan-in for sweeps
pool.msg(tid, "answer")                            # answer a blocked worker / redirect
pool.tasks(); pool.get(tid)                        # records incl. status, cost, output, links
print(pool.transcript(tid))                        # human-rendered agent event stream
pool.cancel(tid)

# rehydration: resume a settled task's session for follow-ups (full memory)
answer = await pool.ask(tid, "why did seed 3 diverge?", output=…)

await pool.serve()                                 # the reconciler daemon
```

The worker's full signature, morally:
`(spec, workspace, budget) → (structured_output: schema, workspace′: gate)` —
the schema types the returned data (SDK `output_format` under the hood,
stamped into the record as `output`), the gate types the side effects.
`run()` is pure sugar over `submit + wait + read output`.

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
3. ~~**Blocked-signaling mechanism.**~~ Resolved (v0.2): `signal_blocked`
   is an in-process custom tool on the worker's SDK session — no shim, no
   PATH games. The `python -m concierge msg` shim remains for humans.
4. **Shepherd as runtime.** Its jailed enforcement (Landlock) + fork/replay
   could give cheaper retries and real isolation, but it's alpha (v0.2.1)
   and single-run scoped. Evaluate once the pool works end-to-end on the
   built-in runtime.
5. ~~**Agent SDK inside the worker process.**~~ Resolved (v0.2):
   implemented as `AgentSdkRuntime` — see the Worker primitive. The key
   line held: the SDK runs the agent loop *in your process*, so it lives in
   the detached worker wrapper, never the daemon; putting it in the daemon
   would kill workers on daemon death, the exact flaw that ruled out
   flightdeck. Cost accepted: `claude-agent-sdk` dependency.
