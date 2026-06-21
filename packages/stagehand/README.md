# stagehand

**[arcadiaimpact.github.io/stagehand](https://arcadiaimpact.github.io/stagehand/)** · a one-page tour.

Primitives + patterns for **orchestrating and monitoring experiment sweeps**. Extracted
from a real research driver (a train → gate → eval → gate → manifest sweep) and
de-coupled from any one experiment.

Three layers, each usable on its own and pure stdlib (zero runtime deps):

| Layer | What it gives you |
|-------|-------------------|
| `monitor`   | a file-backed `running/done/failed` + `done/total` ticker per unit of work; units link via `parent` into a tree |
| `dashboard` | render that tree into one auto-refreshing HTML status page |
| `pipeline`  | the **staircase**: `stage` (barrier) → `gate` (drop the dead) → next stage, plus a `live_dashboard` context and a `headless_handoff` tail |
| `serve`     | put `status.html` behind a Cloudflare quick tunnel for a public live link (needs the `cloudflared` binary) |

```bash
make setup     # uv sync
make test      # unit tests
make example   # run the worked staircase (fake compute) -> runs/status.html
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

## 2. `dashboard` — render the tree

```python
from stagehand import render_dashboard, read_monitors
html = render_dashboard(read_monitors("runs"), started=t0, title="my sweep")
```

One row per unit, children indented under their parent, coloured by state, with
running/done/failed counts on top and a `<meta refresh>` so a browser auto-updates.
The page title and the per-row note (`note_fn: extra dict -> str`) are injectable;
the default note renders `extra` as `k=v · k=v`.

## 3. `pipeline` — the staircase

A sweep is a barrier-separated sequence of **stages**, each followed by a **gate** that
drops unhealthy units *before* the next expensive stage runs. Barriers (not a
free-for-all) because a gate needs the whole previous stage before it can decide what
survives, and the manifest is the single handoff artifact.

```python
from stagehand import stage, gate, live_dashboard, headless_handoff

async with live_dashboard("runs", title="my sweep") as status_html:
    trained = await stage(cells, train_one, concurrency=4)          # barrier
    healthy, failed = gate(trained, gate_train,                     # drop the dead
                           monitor_path=lambda r: r["dir"] / "train.progress.json")

    evals = await stage(healthy, eval_one, concurrency=8)           # barrier
    write_manifest(healthy, failed, evals)

    await headless_handoff(                                         # optional tail
        "Invoke the Workflow tool on runs/manifest.json and report its summary.",
        cwd=repo)
```

- `stage(units, fn, concurrency=N)` — run `fn(unit)` for every unit, at most `N` at once, then gather (the barrier). Results stay in unit order; if `fn` raises despite catching its own errors, the exception is returned *in place* of that unit rather than cancelling the batch.
- `gate(results, predicate, monitor_path=None)` — partition by `predicate(result) -> (ok, issues)` into `(passed, failed)`; mark each failed unit's monitor `failed` (so it shows red on the dashboard).
- `live_dashboard(runs_dir, ...)` — async context manager that polls the tree into `status.html` until the body exits, with a final terminal-state render. Yields the HTML path — serve it however you like (static server + tunnel).
- `headless_handoff(prompt, cwd=..., allowed_tools=..., ...)` — hand the finished manifest to a non-interactive `claude -p`; returns the exit code.

See [`examples/sweep.py`](examples/sweep.py) for the whole staircase wired end-to-end
(with the compute faked, so it runs anywhere in a couple of seconds) — the copy-paste
starting point for a new sweep.

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
        ...   # run the staircase
    finally:
        stop()
```

Or standalone against a dir a sweep is already writing to:

```bash
uv run python examples/serve.py runs      # prints the URL, Ctrl-C to stop
```

The only requirement is the **`cloudflared`** binary on PATH — a binary, not a pip
package, and touched only when you call `serve()`, so the core stays dependency-free.
