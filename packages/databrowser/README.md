# databrowser

A minimal library for browsing JSONL data in the browser. Give it records, it
**validates they share one schema**, builds a self-contained static HTML browser
(sidebar list + full-entry pane), and serves it over a **Cloudflare quick
tunnel** so you can open it from anywhere.

- **Sidebar + main pane** — scroll/`j`/`k` through entries on the left, see the
  selected entry in full on the right.
- **Filtering you declare up front** via `filter_fields` (default: nothing is
  filterable):
  - **categorical** fields → check any **subset of values**
  - **continuous** fields → set any **interval** `[min, max]`
  - kind is **inferred** from the data (numeric → continuous, else categorical),
    or forced with `FilterField(name, "categorical" | "continuous")`.
- Free-text search across all fields (`/` to focus).

## Install

```bash
pip install -e .          # from this directory
```

Serving over a tunnel needs the `cloudflared` binary on `PATH`; without it,
`serve()` falls back to a local URL.

## Library

```python
import databrowser

# Build + serve. filter_fields is the only knob most callers touch.
viewer = databrowser.serve(
    "results.jsonl",
    filter_fields=["model", "split", "score"],   # model/split → categorical, score → continuous
    title="eval results",
)
print(viewer.url)        # https://<...>.trycloudflare.com
# ... browse ...
viewer.stop()            # tear down server + tunnel
```

Force a field's kind, or just build the static site without serving:

```python
from databrowser import build, FilterField

build(
    [{"model": "a", "score": 0.1}, {"model": "b", "score": 0.9}],
    out_dir="site/",
    filter_fields=["model", FilterField("score", "continuous")],
)
# -> site/{index.html, data.jsonl, meta.json}; serve the dir over HTTP to view.
```

`data` may be a path to a `.jsonl` / `.json` file, or an in-memory list of
dicts. Schema validation is strict by default (every record must have the same
keys); pass `strict=False` to browse the union of keys instead.

## CLI

```bash
# infer filter kinds
databrowser serve results.jsonl --filter model,split,score

# force kinds explicitly
databrowser serve results.jsonl --filter-categorical model,split --filter-continuous score

# build only (no serving)
databrowser build results.jsonl --out site/ --filter model
```

## API

| | |
|---|---|
| `serve(data, *, filter_fields=None, title=None, strict=True, out_dir=None, port=None, tunnel=True) -> Viewer` | build + serve; `Viewer.url`, `Viewer.alive`, `Viewer.stop()` |
| `build(data, out_dir, *, filter_fields=None, title=None, strict=True) -> Path` | build the static site only |
| `validate_schema(records, *, strict=True) -> list[str]` | check shared schema; returns field order |
| `FilterField(name, kind=None)` | force a filter field's kind |
