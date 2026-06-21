"""stagehand — primitives + patterns for orchestrating and monitoring experiment runs.

Three layers, each usable on its own:

  monitor   — a file-backed `running/done/failed` + `done/total` primitive per unit
              of work; units link via `parent` into a tree.
  dashboard — render that tree into one auto-refreshing HTML status page.
  pipeline  — the staircase: `stage` (barrier) -> `gate` (drop the dead) -> next
              stage, with a `live_dashboard` context and a `headless_handoff` tail.
"""
from .monitor import monitor, mark, read_monitors, Monitor, SUFFIX
from .dashboard import render_dashboard, default_note, COLORS
from .pipeline import stage, gate, live_dashboard, headless_handoff

__all__ = [
    "monitor", "mark", "read_monitors", "Monitor", "SUFFIX",
    "render_dashboard", "default_note", "COLORS",
    "stage", "gate", "live_dashboard", "headless_handoff",
]
