"""Render a live HTML status tree from a set of monitor files.

`read_monitors()` (from .monitor) returns a flat list of monitor dicts linked by
`parent`; this module turns that into a single auto-refreshing HTML page — one row
per unit, children indented under their parent, coloured by state, with a count of
running/done/failed at the top.

Two bits are domain-specific and injectable:
  - `title`   — the page heading.
  - `note_fn` — `extra dict -> short string`, the per-row status note. The default
                renders `extra` as `k=v · k=v` (skipping the error, shown separately).
A driver usually doesn't call these directly — `pipeline.live_dashboard()` wraps the
polling loop — but they're public so you can render a one-off snapshot.
"""
from __future__ import annotations
import time

COLORS = {"running": "#1a73e8", "done": "#188038", "failed": "#d93025"}


def default_note(extra: dict) -> str:
    """Render `extra` fields as `k=v`, omitting the error (surfaced in its own column)."""
    return " · ".join(f"{k}={v}" for k, v in extra.items() if k != "error")


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


def render_dashboard(monitors, started, *, title="stagehand", note_fn=default_note,
                     refresh=5):
    """Render the monitor list into a self-contained, auto-refreshing HTML page."""
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
