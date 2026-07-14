# stagehand

**[dtch1997.github.io/stagehand](https://dtch1997.github.io/stagehand/)** · a one-page tour.

A tiny **declarative engine for orchestrating steps at scale** — with live monitoring.

You declare *what* work needs doing and how the pieces depend on each other; the engine
figures out *how* to run it: scheduling everything that's ready up to a concurrency cap,
streaming results from one step into the next without barriers, fanning out dynamically,
retrying with feedback, and stopping early when you've got enough. Every step writes a
small file as it runs, so the whole thing renders as one live web page.

A "step" is just an async function — so the same engine drives an experiment sweep, a
fleet of coding agents, a data pipeline, or an eval harness. The core is pure stdlib
(zero runtime deps).

| Layer | What it gives you |
|-------|-------------------|
| `monitor`   | a file-backed `running/done/failed` + `done/total` ticker per unit of work; units link via `parent` into a tree |
| `dashboard` | render that tree into one auto-refreshing HTML status page |
| `engine`    | the **DAG engine**: declare with `Flow.map`/`filter`/`reduce`/`expand`/`add`/`spawn` (+ `best_of`/`with_retry` policies), `await flow.run(stop_when=…)`; streams between steps, fans out, exits early |
| `agents`    | **coding agents as steps**: `agent(flow, prompt, …)` → `AgentOutcome`, behind a backend seam (zero-dep `subprocess_backend`, or any custom async backend) |
| `live`      | `live_dashboard` — poll a running flow's monitor tree and re-render one auto-refreshing HTML status page |
| `artifacts` | **content-addressed inputs/outputs with lineage**: `ArtifactStore` persists files/dirs/secrets by content hash and tracks `inputs` + `produced_by`, behind a backend seam (zero-dep `local_backend`, or the default lazy `ferry_backend()`) |
| `serve`     | put `status.html` behind a public URL for a live link — a lazy wrapper over the shared [`lobby`](https://github.com/dtch1997/lobby) hub: one tunnel + one index page across all runs |
| `manifest`  | **automatic provenance**: every `flow.run()` writes `runs_dir/manifest.json` (git sha/dirty/branch, argv, config, …) and every `store.put()` stamps `meta["git"]` — results always answer *"which code produced this?"* |
| `memo`      | **content-keyed step memoization**: `Flow(memo=…)` persists every successful result keyed on fn source + input values — identical re-runs are free (crashed sweeps resume), changed steps re-run, `run(refresh=True)` deliberately resamples |

```bash
make setup     # uv sync
make test      # unit tests
make example   # run the worked sweep (fake compute) -> runs/status.html
```

## The model

A **step** is an async function. A **node** declares a step over some inputs and returns
a **handle** — a typed placeholder for that step's output(s). You wire dependencies by
passing handles as inputs; the engine infers the edges and runs each step the moment its
inputs are ready. There are **no barriers** except where a step genuinely needs a whole
upstream collection — that's `reduce`. A step that raises is captured (its dependents
skip) without aborting the run.

```python
from stagehand import Flow, live_dashboard

flow = Flow("runs", concurrency=8)
trained = flow.map("train", configs, train_one)        # one task per config
healthy = flow.filter("gate", trained, is_healthy)     # survivors stream on
evals   = flow.map("eval", healthy, eval_one)          # eval_i waits on train_i only
best    = flow.reduce("pick", evals, choose_best)      # the *only* barrier

async with live_dashboard(flow.runs_dir, title="my run") as status_html:
    state = await flow.run(stop_when=lambda s: s.done >= 100)   # early-exit optional
winner = best.result
```

**Nodes** (each returns a handle; pass a handle as a source to wire the edge):
- `flow.map(node, source, fn)` — one task per item of `source` (a static iterable **or** an upstream handle), each running `async fn(item)`; results stream out. `concurrency=` caps the node.
- `flow.filter(node, source, pred)` — `pred(item) -> bool | (ok, issues)`; only survivors propagate, pruned items go red and their dependents skip.
- `flow.reduce(node, source, fn)` — the **barrier**: `fn(list_of_results)` once all upstream tasks are terminal, over the survivors.
- `flow.expand(node, source, fn)` — **dynamic fan-out**: `fn(result) -> iterable`, each element becomes a task (when the width isn't known until runtime).
- `flow.spawn(fn, args, kwargs, name=…, after=…)` — a **single task**; any handle in `args`/`kwargs` becomes a dependency and is substituted with its result (a list of handles reduces over the survivors); `after=[…]` adds ordering-only deps.
- `flow.add(id, fn, deps=[…])` — raw escape hatch for irregular graphs.
- `await flow.run(stop_when=None, check=False)` — schedule the graph, bounded by `concurrency`; returns the final `RunState` (`.results`, `.done`, `.failed`, `.skipped`).

### Policies: fan-out and retry

`best_of` and `with_retry` wrap a step into a richer step — drop them into `map`:

```python
from stagehand import best_of, with_retry

winners = flow.map("solve", units, best_of(solve, n=4, score=lambda r: r["reward"]))
fixed   = flow.map("fix",   units, with_retry(solve, check=parses, max_attempts=3))
```

- `best_of(fn, n, judge=… | score=…)` — run `n` attempts, keep the best (async-or-sync `judge(results) -> index`, or objective `score`). Raisers dropped; losers marked red.
- `with_retry(fn, check=…, max_attempts=3, feedback=None)` — re-run an item that flunks `check(result) -> (ok, issues)` or raises, feeding the prior feedback back in.

Each attempt is `fn(item, attempt=i, feedback=fb)` — your fn opts in by accepting those
keywords (a plain `fn(item)` still works). They nest: `best_of(with_retry(fn, …), n=4, …)`.

Because a handle is a placeholder you **can't `if` on its value while building** — do
result-dependent control flow *inside* a step (a normal coroutine: plain `await`/`if`;
raise to prune), or with `filter` / `expand`.

## Types: compile the graph

Steps declare their I/O with plain hints. `flow.check()` reads them and verifies the graph
can run start to end — every dependency exists, no cycles, and each edge's producer type
fits the consumer — *before* you spend any compute.

```python
async def train(cfg: Config) -> Model: ...
async def evaluate(m: Model) -> Eval: ...

flow.check()                 # raises FlowCheckError on a malformed/ill-typed graph
await flow.run(check=True)   # check first, then run
```

```
flow check failed:
  - map 'eval' <- 'train': expects str, got Model
```

`Handle[T]` is generic, so `.result` / `.results()` are typed in your editor. It's
**gradual and pragmatic** — unannotated == `Any`, subclasses / `list[T]` / `Optional` are
handled, anything it can't reason about passes. A linter, not a type system.

## Coding agents as steps

An agent is just a step. `agent(flow, prompt, …)` spawns a headless coding agent and
returns a structured `AgentOutcome` — so it composes with everything: `best_of` for
best-of-N agents, `with_retry` for retry-with-feedback, `reduce` to merge.

```python
from stagehand import Flow, best_of, agent

flow  = Flow("runs", concurrency=4)
patch = agent(flow, "fix the failing test in foo.py", isolation="worktree")
best  = flow.map("solve", issues, best_of(solve_agent, n=4, judge=pick_best_patch))
await flow.run()
print(patch.result.diff)
```

- `agent(flow, prompt, *inputs, isolation=None, backend=None, tools=…, model=…)` — `prompt` is a string or a callable built from upstream (`agent(flow, lambda issue: f"fix {issue}", issue_h)`). Returns `Handle[AgentOutcome]` (`{ok, summary, diff, cost, tokens, session_id, raw}`).
- `isolation="worktree"` — run the agent in its own throwaway git worktree and capture the diff. Use it whenever agents run in parallel and edit files.
- **Backends** (the seam — the core stays dependency-free):
  - `subprocess_backend` (default) — zero-dep `claude -p --output-format json`.
  - Any `async (AgentSpec) -> AgentOutcome` callable plugs in as a custom backend (per-call `backend=` or global `set_default_backend`), e.g. for richer monitoring.

Any step can stream its own live progress to the dashboard with `current_monitor().set(…)`.

## Serving the dashboard

`live_dashboard` writes `status.html`; `serve` puts it behind a public tunnel:

```python
from stagehand import serve
async with live_dashboard("runs", title="my run"):
    url, stop = serve("runs", name="my-run")   # -> https://<hub>…/a/my-run/status.html
    try:
        await flow.run()
    finally:
        stop()
```

`serve` is a thin lazy wrapper over the [**lobby**](https://github.com/dtch1997/lobby)
library: the dashboard registers with the shared hub daemon, so every run lives under
**one** tunnel URL with a central index page listing all your dashboards and reports
(`name`/`title` label the entry; the tunnel itself is lobby's built-in
pluggable-provider seam, `lobby.tunnel`). lobby is imported only when
you call `serve()`, so the core stays dependency-free — install it with
`pip install git+https://github.com/dtch1997/lobby`.

## The monitor primitive — watch your loops

The ticker exists to make **inner loops** visible on the dashboard: training
steps, eval items, rollout batches. The headline API is `track` — tqdm-shaped,
one tick per iteration, `total` inferred from the iterable:

```python
from stagehand import track

t = track(batches, "train")
for batch in t:
    loss = train_step(batch)
    t.set(loss=loss)             # ride-along fields, flushed with the tick (throttled)
```

`monitor` is the underlying context manager for loops that don't fit an
iterable (`m.update(loss=…)` per step advances the ticker by hand). On clean
exit the state goes `done`; on exception `failed` (error captured) and re-raises.
`mark(path, …)` patches a unit post-hoc; `read_monitors(root)` loads the whole tree.
Monitors are **ephemeral by default** (the progress file is removed on exit); pass
`cleanup=False` to persist the final state (as the engine does for its task files).

**Nesting is automatic.** Open a `track`/`monitor` inside an engine step and it
parents itself to the task's monitor (no `path=`/`parent=` needed), so the
dashboard shows `node → task → your training loop`. When the loop runs in a
**subprocess** — the usual shape for training scripts — pass the linkage
through the environment:

```python
# in the step:
subprocess.run(argv, env={**os.environ, **monitor_env()})

# in the training script — nests under the calling task on the dashboard:
t = track(batches, "train")
for batch in t: ...
```

**What a monitor is NOT for: step status.** The engine already writes a
`running/done/failed` monitor per task — wrapping a whole step in
`monitor(…, total=1)` with `m.set(status="running")` and one `update(n=1)` at
the end duplicates that and shows no progress (the dashboard renders `total=1`
tickers as a dash, and the monitor logs a warning when it sees the pattern).
To attach fields to the *task's* monitor, use `current_monitor().set(…)`.

## Logging

stagehand logs to the stdlib logger `stagehand` with a `NullHandler` — **silent by
default**, so your application decides where logs go. Flow start/finish and `stop_when`
are `INFO`; a task that raises (captured, not re-raised — otherwise only visible on the
dashboard) is `WARNING`; per-task start/done/skip and filter prunes are `DEBUG`.

```python
import stagehand
stagehand.enable_logging("INFO")        # convenience; or configure `logging` yourself
await flow.run()
```
```
INFO stagehand: flow 'sweep' starting — 6 tasks, concurrency=4
WARNING stagehand: ✗ train/2 failed: ValueError('diverged')
INFO stagehand: flow 'sweep' done in 4.2s — 4 ok, 1 failed, 1 skipped
```

## Checks & the cookbook

A step's *body* does the work; a **check** says whether it actually succeeded.
`stagehand.checks` is a small library of reusable correctness predicates — each
returns a `(ok, issues)` result that composes with `&` / `|` / `~`, so it drops
straight into `filter` / `with_retry(check=…)`:

```python
from stagehand.checks import produced, finite, exit_ok
healthy = lambda r: exit_ok(r["exit"]) & produced(r["ckpt"]) & finite(r["loss"])
good = flow.filter("gate", trained, healthy)     # drop diverged / no-checkpoint cells
```

Kernel: `produced` · `exists` · `json_has` · `valid_image` · `finite` · `in_range` ·
`exit_ok` · `tests_pass` · `uri_exists` (the last two shell out to pytest / gcloud).

The [`cookbook/`](cookbook/) collects **reliability recipes**. Abstractly there are
only **two kinds of step** — both "produce → validate → persist a versioned artifact":
- [`cookbook/run_step.py`](cookbook/run_step.py) — **run**: execute the code → validate
  the artifact (`exit_ok &` your check) → persist it + record a pointer; idempotent. One
  recipe for training / eval / plots / reports (the artifact check is a parameter).
- [`cookbook/implementation_step.py`](cookbook/implementation_step.py) — **implement**:
  an agent builds a feature, a **review** gates it, it retries with the review's findings
  ≤ N times, and the approved change is PR'd.

Both use **seams** (compute backend, storage sink, coding agent, review, `gh`) you swap
for your real stack, and compose into the loop: implement → run.

## Artifacts: inputs, outputs & lineage

`stagehand.artifacts` is the storage seam made concrete: provide inputs *upfront*
(datasets, configs, secrets, base adapters) and persist outputs (adapters, eval
results) without losing track of them. Every artifact is identified by the
**content hash of its bytes** — same bytes ⇒ same id ⇒ immutable, dedup'd, and
re-resolvable even if a path moves — and records which artifacts it was derived
from (`inputs`) and which run task produced it (`produced_by`). That's a lineage
DAG you can serialize and re-resolve later.

```python
from stagehand import ArtifactStore

store = ArtifactStore()                                   # ferry.cas-backed by default
ds  = store.put("data/train.jsonl", name="train-data")   # local → uploaded + registered
cfg = store.put("configs/run.yaml", name="config")
key = store.secret("OPENAI_API_KEY")                      # ref-only; value never uploaded

def train(_):
    # ... writes ./out/adapter (a directory) ...
    return store.put("out/adapter", name="lora", inputs=[ds, cfg, key])  # lineage + produced_by

flow = Flow("runs")
adapter = flow.spawn(train, (ds,), name="train")         # Artifacts flow through handles
await flow.run()

p = store.path(adapter.result)        # materialize locally (cached by id, never re-downloaded)
store.save("artifacts.lock.json")     # commit this pointer — re-resolves the whole DAG later
```

Directories (LoRA adapters, checkpoints) are tarred **deterministically** before
hashing, so a dir is content-addressed exactly like a file. Storage lives behind a
backend seam: `local_backend(root)` is a zero-dep content-addressed store on local
disk (used in tests); `ferry_backend(…)` is the default and persists to GCS via
[`ferry.cas`](https://github.com/dtch1997/ferry) (imported lazily, so the core
stays dependency-free; `cloudfs_backend` remains as a back-compat alias). Pass `registry_path=flow.runs_dir / "artifacts.json"` to
mirror the registry alongside the run as it goes; `save()` writes the same shape to
a git-committable lock-file.

## Manifests: which code produced this?

Provenance is automatic. When a flow has a `runs_dir`, `flow.run()` writes
`runs_dir/manifest.json` — git state (sha / dirty / branch / remote), the exact
invocation (argv, cwd, python, host), a timestamp, and the flow's shape. Pass your
resolved experiment config to snapshot it too:

```python
flow = Flow("runs", title="sweep", config={"model": "qwen3-30b", "seed": 0})
```

Every `ArtifactStore.put()` also stamps `{"sha", "dirty"}` into the artifact's
`meta["git"]`, so a committed `artifacts.lock.json` records the code version next
to each artifact's `inputs`/`produced_by`. Outside a git repo both degrade to
`git: null` instead of failing. For ad-hoc scripts there's `write_manifest(path,
config)` / `capture()` directly.

## Memoization: re-running is free

Give a flow a memo store and every successful task result is persisted under a
key derived from **everything that could change the answer** — the step fn's
source (recursing into closure cells, so `best_of`/`with_retry` include the fn
they wrap), its declared inputs, and the *values* of its upstream results:

```python
flow = Flow("runs", memo="runs/memo")
...
await flow.run()               # first time: everything runs
await flow.run()               # again: everything replays, zero work
await flow.run(refresh=True)   # deliberately a NEW experiment: re-run + re-record
```

Change a step's code or an upstream value and that task (plus its downstream)
re-runs; everything untouched replays. A crashed 200-task sweep resumes where it
died. For nondeterministic (LLM-sampling) steps this is the honest semantics:
the persisted samples *are* the experiment, a re-run replays them, and
`refresh=True` is the explicit "draw fresh samples" act. Mark a node
`cache=False` (`map`/`filter`/`reduce`/`add`/`spawn`) to always run it.

Ground rules: only successes are recorded; results must round-trip through JSON
(tuples come back as lists; non-serializable results just always run); unkeyable
inputs degrade to a cache *miss*, never a wrong hit. Cached tasks show
`cached: true` on the dashboard. The store is a plain directory of
`<key>.json` files — share it on a shared filesystem, delete it to drop the
cache.

### Cheap-first: smoke configs, not an engine mode

Every sweep deserves an N=2, five-minute rehearsal that runs the **full** DAG —
analysis and figures included — before the fleet launches. Don't reach for an
engine switch: make the workflow take a config and ship a smaller one next to
the real one.

```python
cfg  = yaml.safe_load(Path(args.config).read_text())   # sweep.yaml | sweep_smoke.yaml
flow = Flow("runs", config=cfg, memo="runs/memo")      # config lands in manifest.json
cells = build_cells(cfg)                               # smoke cfg ⇒ fewer, cheaper cells
```

Because the config's values ride into each step through its *inputs*, the smoke
run's memo keys differ from the real run's automatically — a rehearsal can
never replay into (or out of) the real cache — and `manifest.json` records
exactly which config produced what.

## Examples

Runnable with faked compute, so they go anywhere in a couple of seconds:
- [`examples/sweep.py`](examples/sweep.py) — the core sweep (`map`/`filter`/`reduce`).
- [`examples/fanout_retry.py`](examples/fanout_retry.py) — policies + dynamic `expand`.
- [`examples/agent_fleet.py`](examples/agent_fleet.py) — coding agents as a fleet of steps.
- [`examples/artifacts.py`](examples/artifacts.py) — content-addressed artifacts with lineage (provide → produce → materialize → lock-file → reload & walk lineage).

## Issue tracking

Issues live in-repo under [`.cairn/`](.cairn/), tracked with
[cairn](https://github.com/dtch1997/cairn) (id prefix `stg`). Start with
`cairn ready` to see unblocked work; `cairn prime` prints workflow context.
