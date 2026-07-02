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
| `engine`    | the **DAG engine**: declare with `Flow.map`/`filter`/`reduce`/`expand`/`add` (+ `best_of`/`with_retry` policies), `await flow.run(stop_when=…)`; streams between steps, fans out, exits early |
| `dsl`       | an **imperative-reading surface**: `with flow(…)` + `do`/`fanout`/`retry` — lazy handles that read like straight-line code and compile to the DAG |
| `agents`    | **coding agents as steps**: `agent(prompt, …)` → `AgentOutcome`, behind a backend seam (zero-dep `subprocess_backend`, or the recommended lazy `flightdeck_backend()`) |
| `live`      | `live_dashboard` — poll a running flow's monitor tree and re-render one auto-refreshing HTML status page |
| `artifacts` | **content-addressed inputs/outputs with lineage**: `ArtifactStore` persists files/dirs/secrets by content hash and tracks `inputs` + `produced_by`, behind a backend seam (zero-dep `local_backend`, or the default lazy `cloudfs_backend()`) |
| `serve`     | put `status.html` behind a public tunnel for a live link — a lazy re-export of the standalone [`marquee`](https://github.com/dtch1997/marquee) lib (cloudflared / localhost.run / ngrok) |
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

## Straight-line code: the DSL

The per-task surface — `do(fn, …)` is one task and reads imperatively. It's *lazy*:
`do` returns a placeholder handle, nothing runs until `run()`, and deps are inferred from
the handles you pass.

```python
from stagehand import flow, do, fanout, retry, run

with flow("runs", concurrency=8):
    ckpt  = do(train, cfg)                      # a task, no deps
    good  = do(check, ckpt)                     # handle arg ⇒ dependency
    best  = fanout(solve, good, n=4, score=s)   # same shape as do
    fixed = retry(format, best, check=parses)   # same shape as do
    do(report, after=[fixed])                   # `wait(A); do(B)` — ordering-only dep
    await run(stop_when=lambda s: s.done >= 100)
print(ckpt.result)                              # filled in after the run
```

- `do(fn, *args, after=…)` — one task; handles in args become deps (a **list** of handles reduces over the survivors), `after=[…]` is an ordering-only dep.
- `fanout` / `retry` — the same shape as `do`, wrapping the policies into a node.
- `each(fn, items)` — `[do(fn, x) for x in items]`.

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

An agent is just a step. `agent(prompt, …)` spawns a headless coding agent and returns a
structured `AgentOutcome` — so it composes with everything: `fanout` for best-of-N agents,
`retry` for retry-with-feedback, `reduce` to merge.

```python
from stagehand import flow, fanout, run, agent, flightdeck_backend, set_default_backend

set_default_backend(flightdeck_backend())        # live monitoring (optional; see below)

with flow("runs", concurrency=4):
    patch = agent("fix the failing test in foo.py", isolation="worktree")
    best  = fanout(solve_agent, "implement #42", n=4, judge=pick_best_patch)  # best-of-N
    await run()
print(patch.result.diff)
```

- `agent(prompt, *inputs, isolation=None, backend=None, tools=…, model=…)` — `prompt` is a string or a callable built from upstream (`agent(lambda issue: f"fix {issue}", issue_h)`). Returns `Handle[AgentOutcome]` (`{ok, summary, diff, cost, tokens, session_id, raw}`).
- `isolation="worktree"` — run the agent in its own throwaway git worktree and capture the diff. Use it whenever agents run in parallel and edit files.
- **Backends** (the seam — the core stays dependency-free):
  - `subprocess_backend` (default) — zero-dep `claude -p --output-format json`.
  - `flightdeck_backend()` (recommended) — runs each agent as a [flightdeck](https://github.com/dtch1997/flightdeck) `AgentRun` with live stream-json capture to the dashboard (status / action / tokens / cost / resume). `flightdeck` is imported lazily — an optional integration, not a dependency.

Any step can stream its own live progress to the dashboard with `current_monitor().set(…)`.

## Serving the dashboard

`live_dashboard` writes `status.html`; `serve` puts it behind a public tunnel:

```python
from stagehand import serve
async with live_dashboard("runs", title="my run"):
    url, stop = serve("runs")     # -> https://<random>.trycloudflare.com/status.html
    try:
        await flow.run()
    finally:
        stop()
```

`serve` is a thin lazy re-export of the standalone [**marquee**](https://github.com/dtch1997/marquee)
library, which does the local HTTP server + tunnel behind a pluggable provider seam
(`cloudflared` by default, zero-install `localhost.run` over `ssh`, or `ngrok`). It's
imported only when you call `serve()`, so the core stays dependency-free — install it
with `pip install git+https://github.com/dtch1997/marquee`.

## The monitor primitive

Underneath it all, any unit of work can watch itself — a file-backed `running/done/failed`
+ `done/total` ticker (the engine uses one per task, but it's usable on its own):

```python
from stagehand import monitor
with monitor("cell_s0", total=256, path="runs/cell_s0/train.progress.json",
             parent="sweep") as m:
    for batch in batches:
        m.update(loss=train_step(batch))     # advance + record fields (throttled writes)
```

On clean exit the state goes `done`; on exception `failed` (error captured) and re-raises.
`mark(path, …)` patches a unit post-hoc; `read_monitors(root)` loads the whole tree.
Monitors are **ephemeral by default** (the progress file is removed on exit); pass
`monitor(…, cleanup=False)` to persist the final state (e.g. for a dashboard to read
a finished run — as the engine does for its task files).

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

store = ArtifactStore()                                   # cloudfs-backed by default
ds  = store.put("data/train.jsonl", name="train-data")   # local → uploaded + registered
cfg = store.put("configs/run.yaml", name="config")
key = store.secret("OPENAI_API_KEY")                      # ref-only; value never uploaded

def train(_):
    # ... writes ./out/adapter (a directory) ...
    return store.put("out/adapter", name="lora", inputs=[ds, cfg, key])  # lineage + produced_by

with flow("runs"):
    adapter = do(train, ds, name="train")                # Artifacts flow through handles
    await run()

p = store.path(adapter.result)        # materialize locally (cached by id, never re-downloaded)
store.save("artifacts.lock.json")     # commit this pointer — re-resolves the whole DAG later
```

Directories (LoRA adapters, checkpoints) are tarred **deterministically** before
hashing, so a dir is content-addressed exactly like a file. Storage lives behind a
backend seam: `local_backend(root)` is a zero-dep content-addressed store on local
disk (used in tests); `cloudfs_backend(…)` is the default and persists to GCS via
[`cloudfs`](https://github.com/dtch1997/cloudfs) (imported lazily, so the core
stays dependency-free). Pass `registry_path=flow.runs_dir / "artifacts.json"` to
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
`cache=False` (`map`/`filter`/`reduce`/`add`/`do`) to always run it.

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
- [`examples/sweep.py`](examples/sweep.py) — the engine form (`map`/`filter`/`reduce`).
- [`examples/dsl_demo.py`](examples/dsl_demo.py) — the same sweep in `do`/`fanout`/`retry`.
- [`examples/fanout_retry.py`](examples/fanout_retry.py) — policies + dynamic `expand`.
- [`examples/agent_fleet.py`](examples/agent_fleet.py) — coding agents as a fleet of steps.
- [`examples/artifacts.py`](examples/artifacts.py) — content-addressed artifacts with lineage (provide → produce → materialize → lock-file → reload & walk lineage).

## Issue tracking

Issues live in-repo under [`.cairn/`](.cairn/), tracked with
[cairn](https://github.com/dtch1997/cairn) (id prefix `stg`). Start with
`cairn ready` to see unblocked work; `cairn prime` prints workflow context.
