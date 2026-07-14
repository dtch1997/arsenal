"""The hub index page: server-rendered HTML, lightly enhanced in the browser.

All markup is built here in Python — the inline script only polls `/?partial=1`
for a fresh copy of the app sections, ticks the "ago" timestamps, and wires the
copy-URL buttons. No frameworks, no external assets.
"""

from __future__ import annotations

import html
import time
from itertools import groupby
from pathlib import Path

from . import state

# Fixed hues for the house tools; anything else gets a stable hash-derived hue.
_KIND_HUES = {"stagehand": 262, "databrowser": 208, "cowrite": 175}
_GREY_KINDS = {"app", "static"}

STYLE = """
:root { color-scheme: light dark;
  --bg: #f6f6f3; --panel: #fff; --ink: #23241f; --muted: #75766d;
  --line: #e3e3dc; --brass: #9a7828; --brass-ink: #7d611d;
  --live: #2e8f57; --live-soft: #2e8f5722; --dead: #a84d3a;
  --kind-l: 42%; --kind-s: 38%;
  --shadow: 0 1px 2px rgb(35 36 31 / .05), 0 3px 10px rgb(35 36 31 / .04); }
@media (prefers-color-scheme: dark) {
  :root { --bg: #15170f; --panel: #1d2016; --ink: #e9e8df; --muted: #99988a;
    --line: #32352a; --brass: #d4a83f; --brass-ink: #dcb45a;
    --live: #52b97e; --live-soft: #52b97e26; --dead: #d4715c;
    --kind-l: 68%; --kind-s: 45%; --shadow: none; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 62rem; margin: 0 auto; padding: 0 1.25rem 4rem; }
code, .mono { font-family: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace; }

header { border-bottom: 2px solid var(--brass); background: var(--panel); }
.desk { max-width: 62rem; margin: 0 auto; padding: 1.1rem 1.25rem .95rem;
  display: flex; flex-wrap: wrap; align-items: center; gap: .9rem 1.4rem; }
.wordmark { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  font-size: 1.35rem; letter-spacing: .34em; text-transform: uppercase;
  color: var(--brass-ink); font-weight: 600; text-indent: .05em; }
.urlchip { display: inline-flex; align-items: center; gap: .55rem; min-width: 0;
  border: 1px solid var(--line); border-radius: 999px; background: var(--bg);
  padding: .3rem .45rem .3rem .85rem; font-size: .82rem; max-width: 100%; }
.urlchip .u { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.copy { border: 1px solid var(--line); background: var(--panel); color: var(--muted);
  border-radius: 999px; font: inherit; font-size: .72rem; padding: .12rem .6rem;
  cursor: pointer; flex: none; }
.copy:hover, .copy:focus-visible { color: var(--brass-ink); border-color: var(--brass); }
.copy:focus-visible { outline: 2px solid var(--brass); outline-offset: 1px; }
.deskmeta { color: var(--muted); font-size: .8rem; margin-left: auto;
  display: flex; gap: 1.2rem; white-space: nowrap; }
.deskmeta b { color: var(--ink); font-weight: 500; }

h2 { font-size: .74rem; text-transform: uppercase; letter-spacing: .18em;
  color: var(--muted); font-weight: 600; margin: 2.4rem 0 1rem;
  display: flex; align-items: center; gap: .8rem; }
h2::after { content: ""; flex: 1; border-top: 1px solid var(--line); }
h2 .count { color: var(--brass-ink); }
.project { font-size: .78rem; color: var(--muted); margin: 1.4rem 0 .6rem;
  overflow-wrap: anywhere; }
.project code { color: var(--ink); font-size: .78rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
  gap: .8rem; }

.card { background: var(--panel); border: 1px solid var(--line); border-radius: .35rem;
  padding: .85rem 1rem .8rem; box-shadow: var(--shadow);
  display: flex; flex-direction: column; gap: .45rem;
  border-top: 3px solid hsl(var(--h, 0) var(--s, var(--kind-s)) var(--kind-l)); }
.card.grey { --s: 0%; }
.card.ended { opacity: .62; border-top-color: var(--line); box-shadow: none; }
.row1 { display: flex; align-items: center; gap: .5rem; }
.dot { width: .55rem; height: .55rem; border-radius: 50%; flex: none;
  background: var(--live); box-shadow: 0 0 0 3px var(--live-soft); }
@keyframes breathe { 50% { box-shadow: 0 0 0 6px var(--live-soft); } }
@media (prefers-reduced-motion: no-preference) {
  .dot { animation: breathe 2.6s ease-in-out infinite; } }
.ended .dot { background: var(--muted); box-shadow: none; animation: none; }
a.name { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: .92rem; font-weight: 600; color: var(--ink);
  text-decoration: none; overflow-wrap: anywhere; }
a.name:hover { color: var(--brass-ink); text-decoration: underline; }
.ended a.name { color: var(--dead); }
.chip { margin-left: auto; flex: none; font-size: .68rem; letter-spacing: .06em;
  color: hsl(var(--h, 0) var(--s, var(--kind-s)) var(--kind-l));
  border: 1px solid currentColor; border-radius: 999px; padding: 0 .55rem; opacity: .9; }
.ended .chip { color: var(--muted); }
.title { font-size: .84rem; }
.meta { color: var(--muted); font-size: .74rem; font-variant-numeric: tabular-nums;
  display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
.meta .copy { margin-left: auto; }
.empty { color: var(--muted); margin-top: 2.4rem; }
.empty code { color: var(--ink); }
"""

SCRIPT = """
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".copy");
  if (!b) return;
  const url = b.dataset.path ? new URL(b.dataset.path, location.href).href
                             : b.dataset.copy;
  try { await navigator.clipboard.writeText(url); } catch (err) {}
  const old = b.textContent;
  b.textContent = "copied";
  setTimeout(() => { b.textContent = old; }, 1200);
});

function fmt(s) {
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  return Math.floor(s / 86400) + "d";
}
function tick() {
  const now = Date.now() / 1000;
  for (const el of document.querySelectorAll("[data-ts]"))
    el.textContent = fmt(Math.max(0, Math.floor(now - +el.dataset.ts))) + " ago";
  for (const el of document.querySelectorAll("[data-up]"))
    el.textContent = fmt(Math.max(0, Math.floor(now - +el.dataset.up)));
}
setInterval(tick, 1000);

async function poll() {
  try {
    const r = await fetch("/?partial=1", { cache: "no-store" });
    if (!r.ok) return;
    const text = await r.text();
    const el = document.getElementById("apps");
    if (el && text !== el.innerHTML) { el.innerHTML = text; tick(); }
  } catch (err) {}
}
setInterval(poll, 3000);
"""

# A tiny inline bell favicon so tunnel tabs are findable.
_FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
            "viewBox='0 0 16 16'><text y='13' font-size='13'>\U0001f6ce</text></svg>")


def _dur(ts: float | None) -> str:
    """Compact duration since ts, without the 'ago' (used for hub uptime)."""
    if not ts:
        return "?"
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d"


def _kind_accent(kind: str) -> tuple[str, str]:
    """(extra css class, style attr) giving each kind a stable accent hue."""
    if kind in _GREY_KINDS:
        return "grey", ""
    hue = _KIND_HUES.get(kind, sum(kind.encode()) * 37 % 360)
    return "", f' style="--h:{hue}"'


def _short_cwd(cwd: str | None) -> str:
    if not cwd:
        return ""
    home = str(Path.home())
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def _card(app: dict, dead: bool) -> str:
    name = html.escape(app["name"])
    kind = html.escape(app.get("kind") or "app")
    title = html.escape(app.get("title") or "")
    ts = float(app.get("started_at") or 0)
    grey, style = _kind_accent(app.get("kind") or "app")
    classes = " ".join(c for c in ("card", grey, "ended" if dead else "") if c)
    started = (f'<span data-ts="{ts}">{html.escape(state.ago(ts))}</span>'
               if ts else "<span>?</span>")
    copy_btn = ("" if dead else
                f'<button class="copy" data-path="/a/{name}/">copy url</button>')
    title_div = f'<div class="title">{title}</div>' if title else ""
    return (
        f'<div class="{classes}"{style}>'
        f'<div class="row1"><span class="dot"></span>'
        f'<a class="name" href="/a/{name}/">{name}</a>'
        f'<span class="chip">{kind}</span></div>'
        f"{title_div}"
        f'<div class="meta"><span>port {app["port"]}</span><span>&middot;</span>'
        f"{started}{copy_btn}</div></div>"
    )


def sections(live: list[dict], ended: list[dict]) -> str:
    """The swappable body: Live (grouped by project dir) then Ended."""
    out = []
    if live:
        out.append(f'<h2>Live <span class="count">{len(live)}</span></h2>')
        by_cwd = sorted(live, key=lambda a: (a.get("cwd") or "",
                                             -(a.get("started_at") or 0)))
        groups = [(cwd, list(apps)) for cwd, apps in
                  groupby(by_cwd, key=lambda a: a.get("cwd") or "")]
        groups.sort(key=lambda g: -max(a.get("started_at") or 0 for a in g[1]))
        show_projects = len(groups) > 1
        for cwd, apps in groups:
            if show_projects and cwd:
                out.append(f'<div class="project"><code>'
                           f"{html.escape(_short_cwd(cwd))}</code></div>")
            out.append('<div class="grid">'
                       + "".join(_card(a, dead=False) for a in apps) + "</div>")
    if ended:
        ended = sorted(ended, key=lambda a: -(a.get("started_at") or 0))
        out.append(f'<h2>Ended <span class="count">{len(ended)}</span></h2>')
        out.append('<div class="grid">'
                   + "".join(_card(a, dead=True) for a in ended) + "</div>")
    if not out:
        out.append('<p class="empty">Nothing here yet &mdash; register an app with '
                   "<code>lobby serve &lt;port|dir&gt;</code> or "
                   "<code>lobby.serve(port, name=...)</code>.</p>")
    return "".join(out)


def page(base: str | None, provider: str | None, started_at: float | None,
         live: list[dict], ended: list[dict]) -> str:
    shown_url = base or "local only"
    copy_btn = (f'<button class="copy" data-copy="{html.escape(base, quote=True)}">'
                "copy</button>" if base else "")
    uptime = (f'<span>up <b data-up="{started_at}">{_dur(started_at)}</b></span>'
              if started_at else "")
    tunnel_label = provider or "none (local)"
    return (
        "<!doctype html><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f'<link rel=icon href="{_FAVICON}">'
        f"<title>lobby</title><style>{STYLE}</style>"
        '<header><div class="desk"><span class="wordmark">Lobby</span>'
        f'<span class="urlchip"><span class="u mono">{html.escape(shown_url)}</span>'
        f"{copy_btn}</span>"
        f'<span class="deskmeta"><span>tunnel <b>{html.escape(tunnel_label or "none")}</b></span>'
        f"{uptime}</span></div></header>"
        f'<div class="wrap"><div id="apps">{sections(live, ended)}</div></div>'
        f"<script>{SCRIPT}</script>"
    )


def error_page(code: int, message: str) -> str:
    return (
        f"<!doctype html><meta charset=utf-8><title>lobby: {code}</title>"
        f"<style>{STYLE}</style>"
        f'<div class="wrap"><h2 style="margin-top:3rem">{code}</h2>'
        f"<p>{html.escape(message)}</p>"
        '<p><a href="/">&larr; back to the lobby</a></p></div>'
    )
