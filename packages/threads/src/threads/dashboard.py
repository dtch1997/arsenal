"""Build the dashboard model from the spool and render it two ways:

* :func:`render_markdown` — a markdown digest (``threads render``; future desk
  transport), and
* :func:`render_html` — the served page (``threads serve``): thread table with
  relevance/sort/filter controls, a collapsible tree view (goal/program →
  thread → sessions), a goals↔threads coverage panel, per-thread drill-down,
  the unfiled inbox and candidate threads.

Everything is derived from ``~/.threads`` + the registry + hierarchy.md; no
transcript content is read here (summaries already elided it).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import config, hierarchy, registry, spool
from .relevance import ThreadStats, relevance

SPARK = "▁▂▃▄▅▆▇█"
SORT_KEYS = ("relevance", "last-activity", "sessions", "name")


def _parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _sparkline(days_counts: list[int]) -> str:
    hi = max(days_counts) if days_counts else 0
    if hi == 0:
        return "▁" * len(days_counts)
    return "".join(
        SPARK[min(len(SPARK) - 1, int(c / hi * (len(SPARK) - 1)))]
        for c in days_counts
    )


@dataclass
class Thread:
    slug: str
    sessions: list[dict]
    last_activity: datetime | None
    dormant: bool
    closed: bool
    status_line: str
    relevance: float = 0.0
    sessions_in_window: int = 0
    days_since_last: float | None = None

    @property
    def count(self) -> int:
        return len(self.sessions)

    @property
    def latest_title(self) -> str:
        return self.sessions[0].get("title", "") if self.sessions else ""

    def sparkline(self, now: datetime, days: int = 30) -> str:
        buckets = [0] * days
        for rec in self.sessions:
            ts = _parse_ts(rec.get("t_end")) or _parse_ts(rec.get("t_start"))
            if not ts:
                continue
            age = int((now - ts).total_seconds() // 86400)
            if 0 <= age < days:
                buckets[days - 1 - age] += 1
        return _sparkline(buckets)


@dataclass
class Dashboard:
    threads: list[Thread]
    unfiled: list[dict]
    candidates: list[dict]
    generated_at: datetime
    match_rate: float = 0.0
    dormant_days: int = config.DEFAULT_DORMANT_DAYS
    cfg: config.Config = field(default_factory=config.Config)
    hierarchy: hierarchy.Hierarchy | None = None
    forest: list = field(default_factory=list)
    hierarchy_error: str = ""

    def coverage(self) -> dict:
        """goals with zero active descendant threads, and threads under no goal."""
        hier = self.hierarchy
        if hier is None:
            return {"goals_no_active": [], "threads_no_goal": []}
        active = {t.slug for t in self.threads if t.sessions_in_window > 0}
        goals_no_active = []
        for node in self.forest:
            if node.kind == "goal" and node.sessions_in_window == 0:
                goals_no_active.append(node.slug)
        threads_no_goal = sorted(
            t.slug for t in self.threads
            if _root_kind(hier, t.slug) != "goal")
        return {"goals_no_active": sorted(goals_no_active),
                "threads_no_goal": threads_no_goal, "active": active}


def _root_kind(hier: hierarchy.Hierarchy, slug: str) -> str:
    node = slug
    seen = {slug}
    while node in hier.parent_of:
        node = hier.parent_of[node]
        if node in seen:
            break
        seen.add(node)
    return hier.kind_of.get(node, "thread")


def _artifact_links(rec: dict) -> list[str]:
    arts = rec.get("artifacts") or {}
    out = []
    for pr in arts.get("prs", []):
        out.append(f"PR {pr}")
    for br in arts.get("branches", []):
        out.append(f"branch {br}")
    for url in arts.get("urls", [])[:3]:
        out.append(url)
    return out


def build(*, now: datetime | None = None,
          dormant_days: int | None = None,
          cfg: config.Config | None = None) -> Dashboard:
    now = now or datetime.now(timezone.utc)
    cfg = cfg or config.load_config()
    dormant_days = cfg.dormant_days if dormant_days is None else dormant_days
    reg = registry.load_registry()
    summaries = {r["session_id"]: r for r in spool.load_all_summaries()}
    assignments = spool.load_assignments()
    window = cfg.relevance.window_days

    by_slug: dict[str, list[dict]] = {}
    unfiled: list[dict] = []
    matched = total = 0
    for a in assignments:
        rec = summaries.get(a["session_id"])
        if rec is None:
            continue
        if not rec.get("trivial") or a.get("slug"):
            total += 1
        slug = a.get("slug")
        if slug:
            matched += 1
            by_slug.setdefault(slug, []).append(rec)
        elif not rec.get("trivial"):
            unfiled.append(rec)

    threads = []
    for slug, recs in by_slug.items():
        recs.sort(key=lambda r: (_parse_ts(r.get("t_end")) or datetime.min.replace(
            tzinfo=timezone.utc)), reverse=True)
        last = _parse_ts(recs[0].get("t_end")) or _parse_ts(recs[0].get("t_start"))
        closed = reg.is_closed(slug)
        dormant = (
            not closed and last is not None
            and (now - last).total_seconds() > dormant_days * 86400
        )
        in_window = sum(
            1 for r in recs
            if (ts := _parse_ts(r.get("t_end")) or _parse_ts(r.get("t_start")))
            and (now - ts).total_seconds() <= window * 86400)
        days_since = None if last is None else max(
            0.0, (now - last).total_seconds() / 86400.0)
        score = relevance(ThreadStats(sessions_in_window=in_window,
                                      days_since_last_session=days_since), cfg)
        threads.append(Thread(
            slug=slug, sessions=recs, last_activity=last, dormant=dormant,
            closed=closed, status_line=reg.lines.get(slug, ""),
            relevance=score, sessions_in_window=in_window, days_since_last=days_since,
        ))
    threads.sort(key=lambda t: t.relevance, reverse=True)

    unfiled.sort(key=lambda r: (_parse_ts(r.get("t_end")) or datetime.min.replace(
        tzinfo=timezone.utc)), reverse=True)

    # hierarchy + roll-up forest (tolerant: a broken hierarchy.md never blanks
    # the dashboard — it surfaces the error and falls back to a flat forest).
    hier = None
    forest: list = []
    hier_err = ""
    try:
        hier = hierarchy.load_hierarchy(reg)
    except hierarchy.HierarchyError as e:
        hier_err = str(e)
        hier = hierarchy.Hierarchy()
    stats_by_slug = {
        t.slug: {"sessions": t.count, "sessions_in_window": t.sessions_in_window,
                 "last": t.last_activity, "dormant": t.dormant, "closed": t.closed,
                 "title": t.latest_title, "note": t.status_line}
        for t in threads
    }
    forest = hierarchy.build_forest(hier, stats_by_slug, cfg=cfg, now=now, reg=reg)

    candidates = _load_candidates()
    return Dashboard(
        threads=threads, unfiled=unfiled, candidates=candidates,
        generated_at=now, match_rate=(matched / total if total else 0.0),
        dormant_days=dormant_days, cfg=cfg, hierarchy=hier, forest=forest,
        hierarchy_error=hier_err,
    )


def _load_candidates() -> list[dict]:
    d = config.candidates_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text()
        except OSError:
            continue
        first = text.splitlines()[0].lstrip("# ").strip() if text else p.stem
        members = [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ")]
        out.append({"name": first, "slug": p.stem, "members": members, "body": text})
    return out


# --------------------------------------------------------------------------- #
# sorting + filtering (pure; the server and CLI both call this)
# --------------------------------------------------------------------------- #
@dataclass
class ViewParams:
    sort: str = "relevance"
    filter_active_days: int | None = None
    dormant_only: bool = False
    q: str = ""

    def normalized(self) -> "ViewParams":
        sort = self.sort if self.sort in SORT_KEYS else "relevance"
        return ViewParams(sort=sort, filter_active_days=self.filter_active_days,
                          dormant_only=self.dormant_only, q=(self.q or "").strip())

    def query_string(self) -> str:
        d = {"sort": self.sort}
        if self.filter_active_days is not None:
            d["active_days"] = str(self.filter_active_days)
        if self.dormant_only:
            d["dormant"] = "1"
        if self.q:
            d["q"] = self.q
        return urlencode(d)


def params_from_query(query: dict) -> ViewParams:
    """``query`` is a ``{key: [values]}`` mapping (``urllib.parse.parse_qs``)."""
    def one(k, default=None):
        v = query.get(k)
        return v[0] if isinstance(v, list) and v else (v if v is not None else default)

    active = one("active_days")
    try:
        active_days = int(active) if active not in (None, "", "0") else None
    except (TypeError, ValueError):
        active_days = None
    dormant = str(one("dormant", "")).lower() in ("1", "true", "on", "yes")
    return ViewParams(sort=str(one("sort", "relevance")),
                      filter_active_days=active_days, dormant_only=dormant,
                      q=str(one("q", "") or "")).normalized()


def apply_view(threads: list[Thread], params: ViewParams, now: datetime) -> list[Thread]:
    p = params.normalized()
    out = list(threads)
    if p.dormant_only:
        out = [t for t in out if t.dormant]
    if p.filter_active_days is not None:
        cutoff = p.filter_active_days * 86400
        out = [t for t in out if t.last_activity is not None
               and (now - t.last_activity).total_seconds() <= cutoff]
    if p.q:
        q = p.q.lower()
        out = [t for t in out if q in t.slug.lower()
               or q in (t.latest_title or "").lower()]

    def key(t: Thread):
        if p.sort == "last-activity":
            return t.last_activity or datetime.min.replace(tzinfo=timezone.utc)
        if p.sort == "sessions":
            return t.count
        if p.sort == "name":
            return t.slug
        return t.relevance

    reverse = p.sort != "name"
    out.sort(key=key, reverse=reverse)
    return out


# --------------------------------------------------------------------------- #
# markdown digest
# --------------------------------------------------------------------------- #
def render_markdown(dash: Dashboard | None = None, *, now: datetime | None = None,
                    params: ViewParams | None = None) -> str:
    dash = dash or build(now=now)
    params = (params or ViewParams()).normalized()
    threads = apply_view(dash.threads, params, dash.generated_at)
    L = ["# threads — activity dashboard", ""]
    L.append(
        f"**{len(dash.threads)}** active thread(s), "
        f"**{len(dash.unfiled)}** unfiled session(s), "
        f"**{len(dash.candidates)}** candidate(s). "
        f"Match rate {dash.match_rate * 100:.0f}%. Sorted by {params.sort}.")
    L.append("")
    L.append("## Threads")
    L.append("")
    if not threads:
        L.append("_none_")
    else:
        L.append("| thread | relevance | sessions | last activity | 30d | latest |")
        L.append("|---|---|---|---|---|---|")
        for t in threads:
            flag = " 🔴dormant" if t.dormant else ""
            last = t.last_activity.date().isoformat() if t.last_activity else "?"
            title = t.latest_title.replace("|", "/")[:60]
            L.append(
                f"| {t.slug}{flag} | {t.relevance:.2f} | {t.count} | {last} | "
                f"`{t.sparkline(dash.generated_at)}` | {title} |")
    L.append("")
    L.append("## Hierarchy (roll-ups)")
    L.append("")
    if dash.hierarchy_error:
        L.append(f"⚠️ hierarchy.md: {dash.hierarchy_error}")
    if not dash.forest:
        L.append("_none_")
    else:
        for root in dash.forest:
            _md_tree(L, root, 0)
    L.append("")
    cov = dash.coverage()
    L.append("## Coverage (goals ↔ threads)")
    L.append("")
    L.append(f"- goals with no active descendant thread: "
             f"{', '.join(cov['goals_no_active']) or '_none_'}")
    L.append(f"- threads under no goal: "
             f"{', '.join(cov['threads_no_goal']) or '_none_'}")
    L.append("")
    L.append("## Unfiled inbox")
    L.append("")
    if not dash.unfiled:
        L.append("_none_")
    else:
        for rec in dash.unfiled:
            ts = _parse_ts(rec.get("t_end"))
            when = ts.date().isoformat() if ts else "?"
            L.append(f"- **{when}** — {rec.get('title','')[:80]} "
                     f"(`{rec['session_id'][:8]}`)")
    L.append("")
    L.append("## Candidate threads")
    L.append("")
    if not dash.candidates:
        L.append("_none_")
    else:
        for c in dash.candidates:
            L.append(f"- **{c['name']}** — {len(c['members'])} session(s) "
                     "_(agent-drafted, standing until Daniel edits)_")
    L.append("")
    L.append(f"_generated {dash.generated_at.isoformat()}_")
    L.append("")
    return "\n".join(L)


def _md_tree(L: list[str], node, depth: int) -> None:
    indent = "  " * depth
    last = node.last_activity.date().isoformat() if node.last_activity else "?"
    label = node.title or node.slug
    L.append(f"{indent}- **{label}** ({node.kind}) — {node.sessions} session(s), "
             f"rel {node.relevance:.2f}, last {last}")
    for c in node.children:
        _md_tree(L, c, depth + 1)


# --------------------------------------------------------------------------- #
# html dashboard
# --------------------------------------------------------------------------- #
_CSS = """
body{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:74rem;
margin:1.5rem auto;padding:0 1rem;color:#1a1a1a;background:#fafafa}
h1{font-size:1.4rem}h2{font-size:1.1rem;border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #eee;vertical-align:top}
th{color:#666;font-weight:600}
th a{color:#666;text-decoration:none}th a:hover{text-decoration:underline}
.spark{letter-spacing:1px;color:#2563eb}
.rel{font-variant-numeric:tabular-nums;color:#059669;font-weight:600}
.dormant{color:#b91c1c;font-weight:700}
.pill{display:inline-block;padding:0 .4rem;border-radius:.3rem;background:#eef;font-size:11px}
.k-goal{background:#fde68a}.k-program{background:#c7d2fe}.k-thread{background:#d1fae5}.k-root{background:#e5e7eb}
details{margin:.3rem 0}summary{cursor:pointer}
.tree details{margin:.15rem 0 .15rem .8rem;border-left:1px solid #e5e7eb;padding-left:.6rem}
.muted{color:#888}.art{color:#2563eb}
form.controls{margin:.5rem 0;font-size:13px;display:flex;gap:1rem;flex-wrap:wrap;align-items:center}
form.controls input,form.controls select{font:inherit;font-size:12px}
.warn{color:#b45309;background:#fef3c7;padding:.3rem .5rem;border-radius:.3rem}
@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}
h2{border-color:#333}th,td{border-color:#222}th,th a{color:#999}.pill{background:#223}
.tree details{border-color:#333}.warn{background:#3b2f14;color:#fbbf24}
.k-goal{background:#5b4a12;color:#fde68a}.k-program{background:#312e6b;color:#c7d2fe}
.k-thread{background:#0f3d2e;color:#a7f3d0}.k-root{background:#333;color:#ddd}}
"""


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _sort_header(label: str, key: str, params: ViewParams) -> str:
    p = ViewParams(sort=key, filter_active_days=params.filter_active_days,
                   dormant_only=params.dormant_only, q=params.q)
    mark = " ▾" if params.sort == key else ""
    return f"<th><a href='?{_esc(p.query_string())}'>{_esc(label)}{mark}</a></th>"


def _controls(params: ViewParams) -> str:
    def opt(val, label):
        sel = " selected" if params.sort == val else ""
        return f"<option value='{val}'{sel}>{label}</option>"
    ad = "" if params.filter_active_days is None else str(params.filter_active_days)
    dorm = " checked" if params.dormant_only else ""
    return (
        "<form class='controls' method='get'>"
        "<label>sort <select name='sort'>"
        + opt("relevance", "relevance") + opt("last-activity", "last activity")
        + opt("sessions", "session count") + opt("name", "name")
        + "</select></label>"
        f"<label>active in <input name='active_days' size='3' value='{_esc(ad)}'> days</label>"
        f"<label><input type='checkbox' name='dormant' value='1'{dorm}> dormant only</label>"
        f"<label>search <input name='q' size='16' value='{_esc(params.q)}'></label>"
        "<button type='submit'>apply</button>"
        "</form>")


def render_html(dash: Dashboard | None = None, *, now: datetime | None = None,
                params: ViewParams | None = None, refresh: int | None = None) -> str:
    dash = dash or build(now=now)
    params = (params or ViewParams()).normalized()
    threads = apply_view(dash.threads, params, dash.generated_at)
    meta_refresh = (f"<meta http-equiv='refresh' content='{int(refresh)}'>"
                    if refresh else "")
    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        meta_refresh,
        "<title>threads — activity dashboard</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>threads — activity dashboard</h1>",
        f"<p>{len(dash.threads)} active thread(s) · {len(dash.unfiled)} unfiled · "
        f"{len(dash.candidates)} candidate(s) · match rate "
        f"{dash.match_rate * 100:.0f}% · dormancy &gt;{dash.dormant_days}d · "
        f"relevance τ={dash.cfg.relevance.tau:g}, "
        f"w=({dash.cfg.relevance.w_sessions:g},{dash.cfg.relevance.w_recency:g})</p>",
    ]
    if dash.hierarchy_error:
        out.append(f"<p class='warn'>hierarchy.md ignored: {_esc(dash.hierarchy_error)}</p>")

    # 1. thread table with sort/filter controls
    out.append("<h2>Threads</h2>")
    out.append(_controls(params))
    if not threads:
        out.append("<p class='muted'>none match</p>")
    else:
        out.append("<table><tr>"
                   + _sort_header("thread", "name", params)
                   + _sort_header("relevance", "relevance", params)
                   + _sort_header("sessions", "sessions", params)
                   + _sort_header("last activity", "last-activity", params)
                   + "<th>30-day</th><th>latest session</th></tr>")
        for t in threads:
            flag = " <span class='dormant'>dormant</span>" if t.dormant else ""
            last = t.last_activity.date().isoformat() if t.last_activity else "?"
            out.append(
                f"<tr><td><a href='#t-{_esc(t.slug)}'>{_esc(t.slug)}</a>{flag}</td>"
                f"<td class='rel'>{t.relevance:.2f}</td>"
                f"<td>{t.count}</td><td>{last}</td>"
                f"<td class='spark'>{t.sparkline(dash.generated_at)}</td>"
                f"<td>{_esc(t.latest_title)[:70]}</td></tr>")
        out.append("</table>")

    # 2. tree view (goal/program → thread → sessions)
    out.append("<h2>Tree view <span class='pill'>roll-ups</span></h2>")
    thread_by_slug = {t.slug: t for t in dash.threads}
    if not dash.forest:
        out.append("<p class='muted'>none</p>")
    else:
        out.append("<div class='tree'>")
        for root in dash.forest:
            _html_tree(out, root, thread_by_slug, dash.generated_at)
        out.append("</div>")

    # 3. coverage panel
    cov = dash.coverage()
    out.append("<h2>Coverage <span class='pill'>goals ↔ threads</span></h2>")
    out.append("<p><b>Goals with no active descendant thread:</b> "
               + (", ".join(_esc(g) for g in cov["goals_no_active"])
                  or "<span class='muted'>none</span>") + "</p>")
    out.append("<p><b>Threads under no goal:</b> "
               + (", ".join(_esc(s) for s in cov["threads_no_goal"])
                  or "<span class='muted'>none</span>") + "</p>")

    # 4. per-thread drill-down
    out.append("<h2>Thread drill-down</h2>")
    for t in dash.threads:
        out.append(f"<details id='t-{_esc(t.slug)}'><summary><b>{_esc(t.slug)}</b> "
                   f"— {t.count} session(s) · rel {t.relevance:.2f}"
                   + (" · <span class='dormant'>dormant</span>" if t.dormant else "")
                   + "</summary>")
        if t.status_line:
            out.append(f"<p class='muted'>{_esc(t.status_line)}</p>")
        for rec in t.sessions:
            ts = _parse_ts(rec.get("t_end"))
            when = ts.date().isoformat() if ts else "?"
            arts = _artifact_links(rec)
            art = (" · <span class='art'>" + " · ".join(_esc(a) for a in arts)
                   + "</span>") if arts else ""
            out.append(
                f"<p><b>{when}</b> — {_esc(rec.get('title',''))}<br>"
                f"<span class='muted'>{_esc(rec.get('summary',''))}</span>{art}</p>")
        out.append("</details>")

    # 5. unfiled inbox
    out.append("<h2>Unfiled inbox</h2>")
    if not dash.unfiled:
        out.append("<p class='muted'>none</p>")
    else:
        out.append("<table><tr><th>when</th><th>session</th><th>title</th></tr>")
        for rec in dash.unfiled:
            ts = _parse_ts(rec.get("t_end"))
            when = ts.date().isoformat() if ts else "?"
            out.append(f"<tr><td>{when}</td><td class='muted'>"
                       f"{_esc(rec['session_id'][:8])}</td>"
                       f"<td>{_esc(rec.get('title',''))[:80]}</td></tr>")
        out.append("</table>")

    # 6. candidate threads
    out.append("<h2>Candidate threads "
               "<span class='pill'>agent-drafted, standing until Daniel edits</span></h2>")
    if not dash.candidates:
        out.append("<p class='muted'>none</p>")
    else:
        for c in dash.candidates:
            out.append(f"<details><summary><b>{_esc(c['name'])}</b> — "
                       f"{len(c['members'])} session(s)</summary>"
                       f"<pre>{_esc(c['body'])}</pre></details>")

    out.append(f"<p class='muted'>generated {dash.generated_at.isoformat()}</p>")
    out.append("</body></html>")
    return "\n".join(out)


def _html_tree(out: list[str], node, thread_by_slug: dict, now: datetime) -> None:
    last = node.last_activity.date().isoformat() if node.last_activity else "?"
    label = _esc(node.title or node.slug)
    kls = f"k-{node.kind}"
    dorm = (" · <span class='dormant'>dormant</span>"
            if getattr(node, "dormant", False) else "")
    out.append(
        f"<details open><summary><span class='pill {kls}'>{_esc(node.kind)}</span> "
        f"<b>{label}</b> — {node.sessions} session(s), "
        f"<span class='rel'>rel {node.relevance:.2f}</span>, "
        f"{node.sessions_in_window} in-window, last {last}{dorm}</summary>")
    for c in node.children:
        _html_tree(out, c, thread_by_slug, now)
    # leaf thread: list its sessions as the existing drill-down grain
    if not node.children and node.slug in thread_by_slug:
        t = thread_by_slug[node.slug]
        for rec in t.sessions[:8]:
            ts = _parse_ts(rec.get("t_end"))
            when = ts.date().isoformat() if ts else "?"
            out.append(f"<p class='muted'><b>{when}</b> — "
                       f"{_esc(rec.get('title',''))[:80]}</p>")
    out.append("</details>")
