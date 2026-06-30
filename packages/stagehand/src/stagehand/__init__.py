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
  agents    — coding-agent instances as steps: `agent(prompt, …)` -> AgentOutcome,
              behind a backend seam (zero-dep `subprocess_backend`, or the
              recommended lazy `flightdeck_backend()`); composes with fanout/retry.
  pipeline  — `live_dashboard` (serve the live graph) + `headless_handoff` (hand a
              finished run to a non-interactive `claude -p`).
  serve     — put status.html behind a Cloudflare quick tunnel (needs `cloudflared`).
"""
from ._log import log, enable_logging
from .monitor import monitor, mark, read_monitors, Monitor, SUFFIX
from .dashboard import render_dashboard, default_note, COLORS
from .engine import (Flow, Handle, Task, RunState, FlowCheckError,
                     best_of, with_retry, current_monitor)
from .dsl import flow, do, fanout, retry, each, run, current
from .agents import (agent, AgentOutcome, AgentSpec, subprocess_backend,
                     flightdeck_backend, set_default_backend, DEFAULT_TOOLS)
from .pipeline import live_dashboard, headless_handoff
from .serve import serve, parse_tunnel_url

__all__ = [
    "log", "enable_logging",
    "monitor", "mark", "read_monitors", "Monitor", "SUFFIX",
    "render_dashboard", "default_note", "COLORS",
    "Flow", "Handle", "Task", "RunState", "FlowCheckError", "best_of", "with_retry",
    "current_monitor",
    "flow", "do", "fanout", "retry", "each", "run", "current",
    "agent", "AgentOutcome", "AgentSpec", "subprocess_backend",
    "flightdeck_backend", "set_default_backend", "DEFAULT_TOOLS",
    "live_dashboard", "headless_handoff",
    "serve", "parse_tunnel_url",
]
