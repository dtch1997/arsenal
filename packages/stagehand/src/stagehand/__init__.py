"""stagehand — primitives + a declarative engine for orchestrating and monitoring
experiment runs.

  monitor   — a file-backed `running/done/failed` + `done/total` primitive per unit
              of work; units link via `parent` into a tree.
  dashboard — render that tree into one auto-refreshing HTML status page.
  engine    — the DAG executor: declare work with `Flow.map`/`filter`/`reduce`/
              `expand`/`add` (+ `best_of` / `with_retry` node policies) and
              `await flow.run(stop_when=...)`. The scheduler streams results between
              nodes (no barriers except `reduce`), fans out dynamically, and exits
              early on a criterion.
  dsl       — an imperative-reading surface over the engine: `with flow(...)` +
              `do` / `fanout` / `retry` / `run` (lazy handles that compile to the DAG).
  pipeline  — `live_dashboard` (serve the live graph) + `headless_handoff` (hand a
              finished run to a non-interactive `claude -p`).
  serve     — put status.html behind a Cloudflare quick tunnel (needs `cloudflared`).
"""
from .monitor import monitor, mark, read_monitors, Monitor, SUFFIX
from .dashboard import render_dashboard, default_note, COLORS
from .engine import Flow, Handle, Task, RunState, best_of, with_retry
from .dsl import flow, do, fanout, retry, each, run, current
from .pipeline import live_dashboard, headless_handoff
from .serve import serve, parse_tunnel_url

__all__ = [
    "monitor", "mark", "read_monitors", "Monitor", "SUFFIX",
    "render_dashboard", "default_note", "COLORS",
    "Flow", "Handle", "Task", "RunState", "best_of", "with_retry",
    "flow", "do", "fanout", "retry", "each", "run", "current",
    "live_dashboard", "headless_handoff",
    "serve", "parse_tunnel_url",
]
