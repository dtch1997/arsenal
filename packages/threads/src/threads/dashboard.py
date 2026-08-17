"""Build the dashboard model from the spool and render it two ways:

* :func:`render_markdown` — a markdown digest (``threads render``; future desk
  transport), and
* :func:`render_html` — the served page (``threads serve``): thread table,
  per-thread drill-down, unfiled inbox, candidate threads.

Everything is derived from ``~/.threads`` + the registry; no transcript content
is read here (summaries already elided it).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, registry, spool

SPARK = "▁▂▃▄▅▆▇█"


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
          dormant_days: int = config.DEFAULT_DORMANT_DAYS) -> Dashboard:
    now = now or datetime.now(timezone.utc)
    reg = registry.load_registry()
    summaries = {r["session_id"]: r for r in spool.load_all_summaries()}
    assignments = spool.load_assignments()

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
        threads.append(Thread(
            slug=slug, sessions=recs, last_activity=last, dormant=dormant,
            closed=closed, status_line=reg.lines.get(slug, ""),
        ))
    threads.sort(key=lambda t: (t.last_activity or datetime.min.replace(
        tzinfo=timezone.utc)), reverse=True)

    unfiled.sort(key=lambda r: (_parse_ts(r.get("t_end")) or datetime.min.replace(
        tzinfo=timezone.utc)), reverse=True)

    candidates = _load_candidates()
    return Dashboard(
        threads=threads, unfiled=unfiled, candidates=candidates,
        generated_at=now, match_rate=(matched / total if total else 0.0),
        dormant_days=dormant_days,
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
# markdown digest
# --------------------------------------------------------------------------- #
def render_markdown(dash: Dashboard | None = None, *, now: datetime | None = None) -> str:
    dash = dash or build(now=now)
    L = ["# threads — activity dashboard", ""]
    L.append(
        f"**{len(dash.threads)}** active thread(s), "
        f"**{len(dash.unfiled)}** unfiled session(s), "
        f"**{len(dash.candidates)}** candidate(s). "
        f"Match rate {dash.match_rate * 100:.0f}%.")
    L.append("")
    L.append("## Threads")
    L.append("")
    if not dash.threads:
        L.append("_none_")
    else:
        L.append("| thread | sessions | last activity | 30d | latest |")
        L.append("|---|---|---|---|---|")
        for t in dash.threads:
            flag = " 🔴dormant" if t.dormant else ""
            last = t.last_activity.date().isoformat() if t.last_activity else "?"
            title = t.latest_title.replace("|", "/")[:60]
            L.append(
                f"| {t.slug}{flag} | {t.count} | {last} | "
                f"`{t.sparkline(dash.generated_at)}` | {title} |")
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


# --------------------------------------------------------------------------- #
# html dashboard
# --------------------------------------------------------------------------- #
_CSS = """
body{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:70rem;
margin:1.5rem auto;padding:0 1rem;color:#1a1a1a;background:#fafafa}
h1{font-size:1.4rem}h2{font-size:1.1rem;border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #eee;vertical-align:top}
th{color:#666;font-weight:600}
.spark{letter-spacing:1px;color:#2563eb}
.dormant{color:#b91c1c;font-weight:700}
.pill{display:inline-block;padding:0 .4rem;border-radius:.3rem;background:#eef;font-size:11px}
details{margin:.3rem 0}summary{cursor:pointer}
.muted{color:#888}.art{color:#2563eb}
@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}
h2{border-color:#333}th,td{border-color:#222}th{color:#999}.pill{background:#223}}
"""


def _esc(s) -> str:
    return html.escape(str(s or ""))


def render_html(dash: Dashboard | None = None, *, now: datetime | None = None) -> str:
    dash = dash or build(now=now)
    out = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>threads — activity dashboard</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>threads — activity dashboard</h1>",
        f"<p>{len(dash.threads)} active thread(s) · {len(dash.unfiled)} unfiled · "
        f"{len(dash.candidates)} candidate(s) · match rate "
        f"{dash.match_rate * 100:.0f}% · dormancy &gt;{dash.dormant_days}d</p>",
    ]

    # 1. thread table
    out.append("<h2>Threads</h2>")
    if not dash.threads:
        out.append("<p class='muted'>none</p>")
    else:
        out.append("<table><tr><th>thread</th><th>sessions</th>"
                   "<th>last activity</th><th>30-day</th><th>latest session</th></tr>")
        for t in dash.threads:
            flag = " <span class='dormant'>dormant</span>" if t.dormant else ""
            last = t.last_activity.date().isoformat() if t.last_activity else "?"
            out.append(
                f"<tr><td><a href='#t-{_esc(t.slug)}'>{_esc(t.slug)}</a>{flag}</td>"
                f"<td>{t.count}</td><td>{last}</td>"
                f"<td class='spark'>{t.sparkline(dash.generated_at)}</td>"
                f"<td>{_esc(t.latest_title)[:70]}</td></tr>")
        out.append("</table>")

    # 2. per-thread drill-down
    out.append("<h2>Thread drill-down</h2>")
    for t in dash.threads:
        out.append(f"<details id='t-{_esc(t.slug)}'><summary><b>{_esc(t.slug)}</b> "
                   f"— {t.count} session(s)"
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

    # 3. unfiled inbox
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

    # 4. candidate threads
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
