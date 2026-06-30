# stagehand

**[dtch1997.github.io/stagehand](https://dtch1997.github.io/stagehand/)** · a one-page tour.

A **declarative DAG engine** for **orchestrating and monitoring experiment sweeps**.
You declare *what* work needs doing and how the pieces depend on each other; the
engine figures out *how* to run it — streaming results between stages, fanning out
dynamically, and stopping early when you've got enough. Pure stdlib (zero runtime deps).

| Layer | What it gives you |
|-------|-------------------|
| `monitor`   | a file-backed `running/done/failed` + `done/total` ticker per unit of work; units link via `parent` into a tree |
| `dashboard` | render that tree into one auto-refreshing HTML status page |
| `engine`    | the **DAG engine**: declare a DAG with `Flow.map` / `filter` / `reduce` / `expand` / `add`, add `best_of` / `with_retry` node policies, then `await flow.run(stop_when=…)`. The scheduler streams results between nodes (no barriers except `reduce`), fans out dynamically, and exits early on a criterion |
| `dsl`       | an **imperative-reading surface** over the engine: `with flow(…)` + `do` / `fanout` / `retry` / `run` — lazy handles that read like straight-line code and compile to the DAG |
| `pipeline`  | `live_dashboard` (serve the live graph) + `headless_handoff` (hand a finished run to a non-interactive `claude -p`) |
| `serve`     | put `status.html` behind a Cloudflare quick tunnel for a public live link (needs the `cloudflared` binary) |

```bash
make setup     # uv sync
make test      # unit tests
make example   # run the worked sweep (fake compute) -> runs/status.html
```

## 1. `monitor` — a unit of work that watches itself

Any unit of work wraps its body in `monitor(...)`. It writes a small JSON file that
*anything* can poll; on clean exit the state goes `done`, on exception `failed` (with
the error captured) and the exception re-raises. `parent` links monitors into a tree.

```python
from stagehand import monitor

with monitor("cell_s0", total=256, path="runs/cell_s0/train.progress.json",
             parent="sweep", meta={"phase": "train"}) as m:
    for batch in batches:
        loss = train_step(batch)
        m.update(loss=loss)          # advance the ticker + record fields
```

- `m.update(n=1, **extra)` — advance the ticker, record/overwrite fields (throttled writes).
- `m.set(**extra)` — record fields without advancing (forces a write).
- `mark(path, state=..., extra=...)` — post-hoc patch a unit (e.g. it passed training but flunked a later gate). No-op if the file is gone.
- `read_monitors(root)` — load the whole `*.progress.json` tree.
- `monitor(..., cleanup=True)` — *ephemeral* monitor: the progress file is removed when the context exits (success or failure) instead of being left at its final state. For drivers that keep their own persistent state and only want a live ticker while a unit runs.

## 2. `dashboard` — render the tree

```python
from stagehand import render_dashboard, read_monitors
html = render_dashboard(read_monitors("runs"), started=t0, title="my sweep")
```

One row per unit, children indented under their parent, coloured by state, with
running/done/failed counts on top and a `<meta refresh>` so a browser auto-updates.
The page title and the per-row note (`note_fn: extra dict -> str`) are injectable;
the default note renders `extra` as `k=v · k=v`.

## 3. `engine` — the DAG engine

Declare the DAG; the engine runs it. A node like `map` is a *template* that stamps
out one task per item and wires its dependencies, so the scheduler runs everything
that's *ready* up to a concurrency cap — `eval(cell_0)` starts the moment
`train(cell_0)` finishes, while `train(cell_2)` is still going. **No barriers** except
where a node genuinely needs its whole upstream collection — that's `reduce`.

```python
from stagehand import Flow, live_dashboard

flow = Flow("runs", title="my sweep", concurrency=8)
trained = flow.map("train", cells, train_one)              # one task per cell
healthy = flow.filter("gate", trained, is_healthy)         # survivors stream on
evals   = flow.map("eval", healthy, eval_one)              # eval_i waits on train_i only
best    = flow.reduce("pick", evals, choose_best)          # the *only* barrier

async with live_dashboard(flow.runs_dir, title="my sweep") as status_html:
    state = await flow.run(stop_when=lambda s: s.done >= 100)   # early-exit optional
winner = best.results()[0]
```

Every node call returns a **handle**; pass a handle as a node's source to wire the
dependency (the engine infers the edge), and call `handle.results()` after the run for
that node's results. Each task writes a `monitor` file under `runs_dir/<node>/`, so
`live_dashboard` / `serve` render the live graph with no extra wiring.

**Nodes:**
- `flow.map(node, source, fn, concurrency=None)` — one task per item of `source` (a static iterable **or** an upstream handle), each running `async fn(item)`; results stream into the returned handle. `concurrency` caps this node.
- `flow.filter(node, source, pred)` — like `map`, but each item runs `pred(item) -> bool | (ok, issues)` and only survivors propagate. A pruned item is marked `failed` ("filtered: …") and any task depending on it is **skipped**.
- `flow.reduce(node, source, fn)` — a single **barrier** task running `fn(list_of_results)` once *all* of `source`'s tasks are terminal, over the survivors. The one intended barrier.
- `flow.expand(node, source, fn)` — **dynamic fan-out**: `fn(result) -> iterable`, each element becomes a task in the returned handle (so a downstream `map` runs per element). Use when the fan-out width isn't known until runtime (train emits *K* checkpoints, eval each).
- `flow.add(id, fn, deps=[...])` — raw escape hatch: a single task running `fn(*dep_results)` after the named dependency tasks, for irregular graphs the templates don't cover.

**Run:**
- `await flow.run(stop_when=None)` — schedule the whole graph, bounded by `concurrency`. `stop_when(state) -> bool` is checked after every completion; truthy cancels in-flight tasks and stops. A task that raises is captured (its dependents skip), never aborting the run. Returns the final `RunState` (`.results`, `.done`, `.failed`, `.skipped`).

### Node policies: `best_of` (fan-out) and `with_retry`

These wrap a unit-`fn` into a richer unit-`fn` — drop them into `map`, so they show up as ordinary nodes:

```python
from stagehand import Flow, best_of, with_retry

flow = Flow("runs")
# fan out 4 attempts per item, keep the highest reward
winners = flow.map("solve", units, best_of(solve, n=4, score=lambda r: r["reward"]))
# retry each item (sequentially, with feedback) until it parses
fixed   = flow.map("fix", units, with_retry(solve, check=parses, max_attempts=3))
await flow.run()
```

- `best_of(fn, n, judge=… | score=…, monitor_path=None)` — run `n` attempts of `fn`, keep the best by an async-or-sync `judge(results) -> index` or an objective `score(result) -> float`. Raisers are dropped (all-raise returns the first exception); losers marked `failed` ("not selected").
- `with_retry(fn, check=…, max_attempts=3, feedback=None, monitor_path=None)` — re-run an item that flunks `check(result) -> (ok, issues)` or raises, feeding the previous try's feedback back in. Returns the first pass, else the last failing result; superseded attempts marked `failed`.

Each attempt is called as `fn(item, attempt=i, feedback=fb)` — your `fn` opts in by accepting those keywords (use `attempt` to vary a seed so tries differ); a plain `fn(item)` still works. They nest: `best_of(with_retry(fn, …), n=4, …)`.

See [`examples/sweep.py`](examples/sweep.py) for the whole sweep wired end-to-end and
[`examples/fanout_retry.py`](examples/fanout_retry.py) for the policies + `expand` (both
with the compute faked, so they run anywhere in a couple of seconds).

## 4. `dsl` — straight-line code that builds the DAG

The engine's surface is collection-oriented (`map` over a list). The `dsl` is the
**per-task** surface: `do(fn, …)` is one task, and it reads imperatively. The pieces
are *lazy* — `do` returns a placeholder **handle**, nothing runs until `run()`:

```python
from stagehand import flow, do, fanout, retry, run

with flow("runs", concurrency=8):
    ckpt  = do(train, cell)                    # a task, no deps
    good  = do(check, ckpt)                    # handle arg ⇒ dependency inferred
    best  = fanout(solve, good, n=4, score=s)  # same shape as do
    fixed = retry(format, best, check=parses)  # same shape as do
    do(report, after=[fixed])                  # `wait(A); do(B)` — ordering-only dep
    await run(stop_when=lambda s: s.done >= 100)
print(ckpt.result)                             # filled in after the run
```

- `do(fn, *args, after=…, **kwargs)` — one task running `fn(*args, **kwargs)`. Any **handle** in the args (even nested in a list/dict) becomes a dependency, substituted with its result at run time; `after=[…]` adds ordering-only deps (that's `wait(A); do(B)`). Returns a one-task handle (`h.result` after the run).
- `fanout(fn, unit, n=…, judge=… | score=…)` / `retry(fn, unit, check=…)` — the same shape as `do`, wrapping the `best_of` / `with_retry` policies into a node.
- `each(fn, items)` — `[do(fn, x) for x in items]`. Pass a **list of handles** as one arg to reduce over the survivors: `do(summarize, each(eval, ckpts))`.
- `run(stop_when=…)` / `current()` — run the current `with flow(…)` graph / get its `Flow` (drop down to `Flow.map`/`filter`/`expand` when you need them).

Two rules of thumb: dependencies come from **passing handles**, and because a handle
is a placeholder you **can't `if` on its value while building** — do result-dependent
control flow *inside* a node fn (it's a normal coroutine: plain `await` / `if` / loops;
raise to prune, so dependents skip), or with `Flow.filter` / `expand`.

See [`examples/dsl_demo.py`](examples/dsl_demo.py) for the same sweep in DSL form.

## Serving the dashboard

`live_dashboard` only *writes* `status.html`. `serve` is the other half — it puts that
directory behind a local `http.server` + a [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
so you can watch the run from anywhere:

```python
from stagehand import serve
url, stop = serve("runs")     # -> https://<random>.trycloudflare.com/status.html
...                            # the page's <meta refresh> keeps it current
stop()                         # tear down server + tunnel
```

In-process, alongside a live sweep:

```python
async with live_dashboard("runs", title="my sweep"):
    url, stop = serve("runs")
    print("watch:", url)
    try:
        await flow.run()
    finally:
        stop()
```

Or standalone against a dir a sweep is already writing to:

```bash
uv run python examples/serve.py runs      # prints the URL, Ctrl-C to stop
```

The only requirement is the **`cloudflared`** binary on PATH — a binary, not a pip
package, and touched only when you call `serve()`, so the core stays dependency-free.
