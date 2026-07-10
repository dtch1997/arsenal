"""Render a live status view of a stagehand run (or any monitor tree).

`render_dashboard(monitors, started, *, graph=...)` turns the flat list of monitor
dicts (from `read_monitors`) into one self-contained, auto-refreshing HTML page —
deliberately generic: it renders *whatever monitor files exist*, with no
assumptions about how the work is shaped.

  - a **status table** of every unit of work — its state
    (running/done/failed/pruned), `done/total` progress, any extra fields, and its
    error — grouped node → task via the monitors' `parent` links; and
  - when the node topology is present (`graph` from `read_graph`, i.e.
    `runs_dir/graph.json`), a **DAG** drawn as a mermaid `flowchart LR`, every node
    a box coloured by its aggregate state and labelled with its kind + done/total.

`title` and `note_fn` (an ``extra dict -> short string`` per-row note) stay
injectable. A `failed` task whose error is a `filter`/`best_of`/`retry` marker
shows as `pruned` rather than a hard failure (reflecting the engine's policies).
"""
from __future__ import annotations
import re
import time

COLORS = {"running": "#1a73e8", "done": "#188038", "failed": "#d93025",
          "pruned": "#f9ab00", "pending": "#9aa0a6"}
_PRUNE_PREFIXES = ("filtered", "best_of", "retry")   # "failed" markers that mean pruned


def default_note(extra: dict) -> str:
    """Render `extra` fields as `k=v`, omitting the error (surfaced separately)."""
    return " · ".join(f"{k}={v}" for k, v in extra.items() if k != "error")


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _state_of(m) -> str:
    """A monitor's dashboard state. A `failed` monitor whose error is a
    prune/supersede marker (filter / best_of loser / retry) shows as `pruned`."""
    st = m.get("state", "pending")
    if st == "failed":
        err = (m.get("extra") or {}).get("error") or ""
        if err.startswith(_PRUNE_PREFIXES):
            return "pruned"
    return st


# --- node aggregation (for the DAG, when a graph is present) ---------------- #
def _task_monitors(monitors):
    return [m for m in monitors if "/" in m["name"]]    # task ids are "node/i"


def _node_counts(monitors) -> dict:
    """Per-node tallies from the task monitors: {node: {total, done, running, …}}."""
    cnt = {}
    for m in _task_monitors(monitors):
        node = (m.get("meta") or {}).get("node") or m["name"].split("/")[0]
        d = cnt.setdefault(node, {"total": 0})
        d["total"] += 1
        st = _state_of(m)
        d[st] = d.get(st, 0) + 1
    return cnt


def _node_state(c) -> str:
    """Collapse a node's per-task counts into one state for colouring."""
    if c.get("running"):
        return "running"
    if c.get("failed"):
        return "failed"
    if c.get("total") and c.get("done", 0) + c.get("pruned", 0) == c["total"]:
        return "done"
    return "running" if c.get("done") else "pending"


# --- the DAG panel (mermaid) ----------------------------------------------- #
def _mid(name):
    return "n_" + re.sub(r"\W", "_", name)


_MERMAID_CLASS = {"running": ("#e8f0fe", "#1a73e8"), "done": ("#e6f4ea", "#188038"),
                  "failed": ("#fce8e6", "#d93025"), "pending": ("#f1f3f4", "#9aa0a6")}


def _mermaid(graph, counts) -> str:
    """A `flowchart LR` of the node graph, coloured by aggregate state."""
    lines, used = ["flowchart LR"], set()
    for nd in graph["nodes"]:
        n = nd["name"]
        c = counts.get(n, {})
        st = _node_state(c)
        used.add(st)
        tag = nd["kind"] if nd["kind"] in ("map", "filter", "expand", "reduce") else ""
        sub = (f"{tag} · " if tag else "") + f'{c.get("done", 0)}/{c.get("total", 0)}'
        if c.get("failed"):
            sub += f' · {c["failed"]}✗'
        label = f'{_esc(n)}<br><small>{sub}</small>'
        lines.append(f'  {_mid(n)}["{label}"]:::{st}')
    for a, b in graph["edges"]:
        lines.append(f'  {_mid(a)} --> {_mid(b)}')
    for st in used:
        bg, fg = _MERMAID_CLASS.get(st, _MERMAID_CLASS["pending"])
        lines.append(f'  classDef {st} fill:{bg},stroke:{fg},color:{fg}')
    return "\n".join(lines)


def _dag_section(graph, monitors) -> str:
    if not graph or not graph.get("nodes"):
        return ""
    merm = _mermaid(graph, _node_counts(monitors))
    return (
        '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>'
        f'<section><div class=mermaid>{merm}</div></section>'
        '<script>mermaid.initialize({startOnLoad:true,securityLevel:"loose"});</script>')


# --- the status table ------------------------------------------------------ #
def _unit_key(m):
    """Sort key: by node, then numeric task index (so train/2 < train/10)."""
    node, _, idx = m["name"].rpartition("/")
    return (node, int(idx) if idx.isdigit() else 0, m["name"])


def _row(m, indent, note_fn) -> str:
    st = _state_of(m)
    pad = "&nbsp;" * (4 * indent)
    ex = m.get("extra") or {}
    note = note_fn(ex)
    err = (ex.get("error") or "")[:90]
    return (f'<tr><td>{pad}{_esc(m["name"])}</td>'
            f'<td class=state style="background:{COLORS.get(st, "#000")}">{st}</td>'
            f'<td class=prog>{m.get("done", 0)}/{m.get("total", 0)}</td>'
            f'<td>{_esc(note)}</td><td class=err>{_esc(err)}</td></tr>')


def _status_table(monitors, note_fn) -> str:
    """Every unit as a row, grouped node → task via `parent` (orphans are roots)."""
    by_name = {m["name"]: m for m in monitors}
    children, roots = {}, []
    for m in monitors:
        par = m.get("parent")
        (children.setdefault(par, []).append(m) if par in by_name else roots.append(m))
    rows = []
    for r in sorted(roots, key=lambda m: m["name"]):
        rows.append(_row(r, 0, note_fn))
        for c in sorted(children.get(r["name"], []), key=_unit_key):
            rows.append(_row(c, 1, note_fn))
    return ('<table class=status><tr><th>unit</th><th>state</th><th>progress</th>'
            '<th>note</th><th>error</th></tr>' + "".join(rows) + '</table>')


_STYLE = """body{font:14px system-ui;margin:24px;color:#202124}
h2 span{color:#5f6368;font-weight:400}.muted{color:#9aa0a6}
section{margin:18px 0}
.mermaid{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px}
table.status{border-collapse:collapse;width:100%}
table.status td,table.status th{border-bottom:1px solid #eee;padding:4px 8px}
table.status th{text-align:left;color:#5f6368;font-weight:600}
td.state{color:#fff;text-align:center;border-radius:4px}
td.prog{text-align:right;white-space:nowrap}
td.err{color:#d93025}"""


def render_dashboard(monitors, started, *, title="stagehand", note_fn=default_note,
                     refresh=5, graph=None):
    """Render the monitor list (+ optional `graph` topology) into a self-contained,
    auto-refreshing HTML page: a generic status table, plus a DAG when a topology
    is available. Makes no assumptions about how the work is shaped."""
    units = _task_monitors(monitors) or monitors
    counts = {}
    for m in units:
        st = _state_of(m)
        counts[st] = counts.get(st, 0) + 1
    chips = " ".join(f'<b style="color:{COLORS.get(k, "#000")}">{v} {k}</b>'
                     for k, v in sorted(counts.items()))
    el = int(time.time() - started)
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content={refresh}>
<title>{_esc(title)}</title>
<style>{_STYLE}</style>
<h2>{_esc(title)} <span>· {el // 3600}h{el % 3600 // 60:02d}m elapsed
 · {len(units)} units</span></h2>
<p>{chips}</p>
{_dag_section(graph, monitors)}
<section>{_status_table(monitors, note_fn)}</section>
<p class=muted>auto-refreshes every {refresh}s</p>"""
