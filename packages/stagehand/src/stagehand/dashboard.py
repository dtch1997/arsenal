"""Render a live view of a stagehand Flow: its process DAG and per-item flow.

`render_dashboard(monitors, started, *, graph=...)` turns the flat list of monitor
dicts (from `read_monitors`) plus the node topology (from `read_graph`, i.e.
`runs_dir/graph.json`) into one self-contained, auto-refreshing HTML page with two
coordinated panels:

  1. a mermaid ``flowchart LR`` of the *node* graph — every node a box in
     topological order, edges drawn, coloured by aggregate state and labelled with
     its kind + done/total; and
  2. a CSS *swimlane* — one row per source-item lineage, one column per fanned-out
     ("spine") node in topo order, each cell coloured by that item's per-stage
     state (done / running / failed / pruned). Barrier nodes (reduce/gather)
     render as a footer band; standalone singleton tasks as cards.

Lineage is reconstructed from each task's ``deps`` (the engine writes them into the
monitor ``meta``); the spine is the set of nodes that actually fanned out (≥2
tasks), so it works whether the graph was built with ``Flow.map``/``filter`` or the
``do`` DSL (where every node is kind ``task``). With ``graph=None`` (no graph.json
yet) it falls back to the legacy flat status table, so an old runs_dir still
renders. Two bits stay injectable: `title` and `note_fn` (an ``extra dict -> short
string`` per-row note).

Known v1 limitation: dynamic ``expand`` fan-out (1 upstream -> K elements) seeds
synthetic element tasks that aren't written as progress files, so a ``map``
downstream of an ``expand`` begins fresh lineage rows rather than splitting the
upstream row.
"""
from __future__ import annotations
import re
import time

COLORS = {"running": "#1a73e8", "done": "#188038", "failed": "#d93025",
          "pruned": "#f9ab00", "pending": "#9aa0a6"}
GLYPH = {"running": "▓", "done": "✓", "failed": "✗", "pruned": "✂", "pending": "·"}
_BARRIER_KINDS = ("reduce", "gather")
_PRUNE_PREFIXES = ("filtered", "best_of", "retry")   # "failed" markers that mean pruned


def default_note(extra: dict) -> str:
    """Render `extra` fields as `k=v`, omitting the error (surfaced separately)."""
    return " · ".join(f"{k}={v}" for k, v in extra.items() if k != "error")


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --- task / node model from the monitor files ------------------------------ #
def _task_monitors(monitors):
    return [m for m in monitors if "/" in m["name"]]    # task ids are "node/i"


def _state_of(m) -> str:
    """A task monitor's dashboard state. A `failed` monitor whose error is a
    prune/supersede marker (filter / best_of loser / retry) shows as `pruned`."""
    st = m.get("state", "pending")
    if st == "failed":
        err = (m.get("extra") or {}).get("error") or ""
        if err.startswith(_PRUNE_PREFIXES):
            return "pruned"
    return st


def _index(monitors) -> dict:
    """Index task monitors by id -> {id, node, deps, state, extra}."""
    tasks = {}
    for m in _task_monitors(monitors):
        meta = m.get("meta") or {}
        node = meta.get("node") or m["name"].split("/")[0]
        tasks[m["name"]] = {"id": m["name"], "node": node,
                            "deps": list(meta.get("deps") or []),
                            "state": _state_of(m), "extra": m.get("extra") or {}}
    return tasks


def _node_counts(tasks) -> dict:
    """Per-node tallies: {node: {total, done, running, failed, pruned, ...}}."""
    cnt = {}
    for t in tasks.values():
        d = cnt.setdefault(t["node"], {"total": 0})
        d["total"] += 1
        d[t["state"]] = d.get(t["state"], 0) + 1
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


def _classify(graph, counts):
    """Split nodes (in topo order) into swimlane spine / barrier footer / cards.

    A barrier (reduce/gather) is a footer band; a node that fanned out (≥2 tasks)
    is a swimlane column; everything else (singleton tasks) is a card.
    """
    kinds = {n["name"]: n["kind"] for n in graph["nodes"]}
    order = [n["name"] for n in sorted(graph["nodes"],
                                       key=lambda n: (n["rank"], n["name"]))]
    spine, footer, cards = [], [], []
    for n in order:
        total = counts.get(n, {}).get("total", 0)
        if kinds.get(n) in _BARRIER_KINDS:
            footer.append(n)
        elif total >= 2:
            spine.append(n)
        else:
            cards.append(n)
    return kinds, order, spine, footer, cards


def _rows(tasks, spine):
    """Group spine tasks into lineage rows by tracing `deps` to a root task."""
    sset = set(spine)
    spine_tasks = {tid: t for tid, t in tasks.items() if t["node"] in sset}

    def root(tid):
        cur, seen = tid, set()
        while cur not in seen:
            seen.add(cur)
            ups = [d for d in spine_tasks[cur]["deps"] if d in spine_tasks]
            if not ups:
                return cur
            cur = ups[0]
        return cur                       # defensive: cycle (shouldn't happen)

    rows = {}
    for tid, t in spine_tasks.items():
        rows.setdefault(root(tid), {}).setdefault(t["node"], t)
    return rows


def _row_key(rid):
    node, _, idx = rid.rpartition("/")
    return (node, int(idx) if idx.isdigit() else 0, rid)


# --- panels ---------------------------------------------------------------- #
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


def _swimlane(rows, spine, counts) -> str:
    head = ["<th></th>"] + [
        f'<th>{_esc(n)}<br><small>{counts.get(n, {}).get("done", 0)}'
        f'/{counts.get(n, {}).get("total", 0)}</small></th>' for n in spine]
    body = []
    for rid in sorted(rows, key=_row_key):
        cells = [f'<td class=rowlabel>{_esc(rid)}</td>']
        for n in spine:
            t = rows[rid].get(n)
            if t is None:
                cells.append('<td class=cell></td>')
                continue
            st = t["state"]
            note = default_note(t["extra"])
            err = t["extra"].get("error", "")
            tip = _esc(f'{t["id"]} · {st}'
                       + (f' · {note}' if note else "")
                       + (f' · {err}' if err else ""))
            cells.append(f'<td class=cell style="background:{COLORS[st]}" '
                         f'title="{tip}">{GLYPH[st]}</td>')
        body.append(f'<tr>{"".join(cells)}</tr>')
    return f'<table class=swim><tr>{"".join(head)}</tr>{"".join(body)}</table>'


def _node_note_err(node, tasks, note_fn):
    sample = [t for t in tasks.values() if t["node"] == node]
    note = note_fn(sample[0]["extra"]) if sample else ""
    err = ""
    for t in sample:
        if t["extra"].get("error"):
            err = t["extra"]["error"][:90]
            break
    return note, err


def _band(node, counts, tasks, note_fn) -> str:
    c = counts.get(node, {})
    st = _node_state(c)
    note, err = _node_note_err(node, tasks, note_fn)
    return (f'<div class=band style="border-left-color:{COLORS[st]}">'
            f'<b>{_esc(node)}</b> '
            f'<span class=tag style="background:{COLORS[st]}">{st}</span> '
            f'{c.get("done", 0)}/{c.get("total", 0)} '
            f'<span class=note>{_esc(note)}</span> '
            f'<span class=err>{_esc(err)}</span></div>')


def _card(node, counts, tasks, note_fn) -> str:
    c = counts.get(node, {})
    st = _node_state(c)
    note, err = _node_note_err(node, tasks, note_fn)
    return (f'<div class=card><div class=cardhead style="background:{COLORS[st]}">'
            f'{_esc(node)}</div><div class=cardbody>'
            f'{c.get("done", 0)}/{c.get("total", 0)}'
            f'<br><small>{_esc(note)}</small>'
            + (f'<br><small class=err>{_esc(err)}</small>' if err else "")
            + '</div></div>')


_STYLE = """body{font:14px system-ui;margin:24px;color:#202124}
h2 span,h3{color:#5f6368;font-weight:400}.muted{color:#9aa0a6}
section{margin:18px 0}
.mermaid{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px}
table.swim{border-collapse:separate;border-spacing:3px}
table.swim th{font-weight:600;color:#5f6368;padding:2px 8px;text-align:center;font-size:12px}
table.swim td.cell{width:24px;height:24px;text-align:center;color:#fff;border-radius:4px}
table.swim td.rowlabel{color:#5f6368;font-size:12px;padding-right:10px;white-space:nowrap}
.band{border:1px solid #eee;border-left:4px solid #888;border-radius:6px;padding:6px 10px;margin:4px 0}
.tag{color:#fff;border-radius:4px;padding:0 6px;font-size:12px}
.cards{display:flex;flex-wrap:wrap;gap:8px}
.card{border:1px solid #eee;border-radius:6px;overflow:hidden;min-width:90px}
.cardhead{color:#fff;padding:3px 8px;font-size:12px;font-weight:600}
.cardbody{padding:6px 8px}
.err{color:#d93025}.note{color:#5f6368}small{color:#5f6368}"""


def render_dashboard(monitors, started, *, title="stagehand", note_fn=default_note,
                     refresh=5, graph=None):
    """Render the monitor list (+ optional `graph` topology) into a self-contained,
    auto-refreshing HTML page. With no `graph`, falls back to the legacy flat table."""
    if not graph or not graph.get("nodes"):
        return _render_flat(monitors, started, title=title, note_fn=note_fn,
                            refresh=refresh)
    tasks = _index(monitors)
    counts = _node_counts(tasks)
    _, _, spine, footer, cards = _classify(graph, counts)
    rows = _rows(tasks, spine)

    chip_counts = {}
    for t in tasks.values():
        chip_counts[t["state"]] = chip_counts.get(t["state"], 0) + 1
    chips = " ".join(f'<b style="color:{COLORS.get(k, "#000")}">{v} {k}</b>'
                     for k, v in sorted(chip_counts.items()))
    el = int(time.time() - started)
    merm = _mermaid(graph, counts)
    swim = (_swimlane(rows, spine, counts) if spine
            else '<p class=muted>no fanned-out stages yet</p>')
    foot = "".join(_band(n, counts, tasks, note_fn) for n in footer)
    crds = "".join(_card(n, counts, tasks, note_fn) for n in cards)
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content={refresh}>
<title>{_esc(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>{_STYLE}</style>
<h2>{_esc(title)} <span>· {el // 3600}h{el % 3600 // 60:02d}m elapsed
 · {len(graph["nodes"])} nodes</span></h2>
<p>{chips}</p>
<section><div class=mermaid>{merm}</div></section>
<section><h3>item flow</h3>{swim}</section>
{f'<section>{foot}</section>' if foot else ''}
{f'<section><div class=cards>{crds}</div></section>' if crds else ''}
<script>mermaid.initialize({{startOnLoad:true,securityLevel:"loose"}});</script>
<p class=muted>auto-refreshes every {refresh}s</p>"""


# --- legacy flat table (fallback when there's no graph.json) ---------------- #
def _row(m, indent, note_fn):
    st = m["state"]
    pad = "&nbsp;" * (4 * indent)
    bar = f'{m["done"]}/{m["total"]}'
    ex = m.get("extra", {})
    note = note_fn(ex)
    err = (ex.get("error") or "")[:90]
    return (f'<tr><td>{pad}{m["name"]}</td>'
            f'<td style="color:#fff;background:{COLORS.get(st, "#000")};text-align:center">{st}</td>'
            f'<td style="text-align:right">{bar}</td><td>{note}</td>'
            f'<td style="color:#d93025">{err}</td></tr>')


def _render_flat(monitors, started, *, title="stagehand", note_fn=default_note,
                 refresh=5):
    """The original parent/child status table — used when no topology is available."""
    by_name = {m["name"]: m for m in monitors}
    children, roots = {}, []
    for m in monitors:
        par = m.get("parent")
        (children.setdefault(par, []).append(m) if par in by_name else roots.append(m))
    counts = {}
    for m in monitors:
        counts[m["state"]] = counts.get(m["state"], 0) + 1
    chips = " ".join(f'<b style="color:{COLORS.get(k, "#000")}">{v} {k}</b>'
                     for k, v in sorted(counts.items()))
    rows = []
    for r in sorted(roots, key=lambda m: m["name"]):
        rows.append(_row(r, 0, note_fn))
        for c in sorted(children.get(r["name"], []), key=lambda m: m["name"]):
            rows.append(_row(c, 1, note_fn))
    el = int(time.time() - started)
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content={refresh}>
<title>{title}</title>
<style>body{{font:14px system-ui;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #eee;padding:4px 8px}}th{{text-align:left;color:#666}}</style>
<h2>{title} <span style="color:#999">· {el // 3600}h{el % 3600 // 60:02d}m elapsed
 · {len(roots)} units</span></h2>
<p>{chips}</p>
<table><tr><th>unit</th><th>state</th><th>progress</th><th>note</th><th>error</th></tr>
{''.join(rows)}</table>
<p style="color:#999">auto-refreshes every {refresh}s</p>"""
