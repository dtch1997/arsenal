"""stagehand — primitives + a declarative engine for orchestrating and monitoring
experiment runs.

  monitor   — a file-backed `running/done/failed` + `done/total` primitive per unit
              of work; units link via `parent` into a tree.
  dashboard — render the run as one auto-refreshing HTML status page: a generic
              table of every unit (state/progress/note/error), plus a mermaid DAG
              of the nodes when a `graph.json` topology is present. Renders
              whatever monitor files exist — no assumptions about the work's shape.
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
  checks    — reusable correctness predicates (`produced`/`finite`/`exit_ok`/
              `tests_pass`/`valid_image`/…) returning a composable `(ok, issues)`
              gate result; the per-step-type recipes in `cookbook/` build on them.
  live      — `live_dashboard`: poll a running flow's monitor tree and re-render
              one auto-refreshing HTML status page until you're done.
  artifacts — content-addressed inputs/outputs with lineage: `ArtifactStore`
              persists files/dirs/secrets by content hash (records `inputs` +
              `produced_by`), behind a backend seam (zero-dep `local_backend`, or
              the default lazy `cloudfs_backend()`).
  serve     — put status.html behind a Cloudflare quick tunnel (needs `cloudflared`).
"""
from ._log import log, enable_logging
from .monitor import monitor, mark, read_monitors, read_graph, Monitor, SUFFIX
from .dashboard import render_dashboard, default_note, COLORS
from .engine import (Flow, Handle, Task, RunState, FlowCheckError,
                     best_of, with_retry, current_monitor)
from .dsl import flow, do, fanout, retry, each, run, current
from .agents import (agent, AgentOutcome, AgentSpec, subprocess_backend,
                     flightdeck_backend, set_default_backend, DEFAULT_TOOLS)
from .live import live_dashboard
from .artifacts import (Artifact, ArtifactStore, local_backend, cloudfs_backend,
                        set_default_artifact_backend)
from .serve import serve, parse_tunnel_url
from . import checks

__all__ = [
    "log", "enable_logging", "checks",
    "monitor", "mark", "read_monitors", "read_graph", "Monitor", "SUFFIX",
    "render_dashboard", "default_note", "COLORS",
    "Flow", "Handle", "Task", "RunState", "FlowCheckError", "best_of", "with_retry",
    "current_monitor",
    "flow", "do", "fanout", "retry", "each", "run", "current",
    "agent", "AgentOutcome", "AgentSpec", "subprocess_backend",
    "flightdeck_backend", "set_default_backend", "DEFAULT_TOOLS",
    "live_dashboard",
    "Artifact", "ArtifactStore", "local_backend", "cloudfs_backend",
    "set_default_artifact_backend",
    "serve", "parse_tunnel_url",
]
