"""``threads vault`` — an Obsidian-compatible mirror of the rendered layer.

Decision (Daniel, 2026-08-17): make the on-disk *rendered* layer
Obsidian-compatible so Obsidian works as an optional richer browser. The JSON
spool stays machine truth; ``~/.threads/vault/`` is a regenerable *view*:

    vault/
      threads/<slug>.md         frontmatter + digest + [[session-…]] + [[parent]]
      sessions/session-<id>.md  frontmatter + title + summary + artifact links
      programs/<slug>.md        hierarchy node + [[child]] links + roll-up stats
      goals/<slug>.md           hierarchy root + [[child]] links
      candidates/<slug>.md      auto-drafted clusters
      INDEX.md                  dashboard-in-markdown (top / dormant / unfiled)

Rules: valid YAML frontmatter; wikilinks resolve within the vault (filenames =
registry slugs, so mounting ``~/jarvis-memory`` alongside cross-links for
free); full regeneration is idempotent and prunes notes whose source records
vanished; transcript content stays summarized exactly as in the spool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import config, dashboard, spool

# subdirs this tool fully owns (regenerate + prune). NEVER jarvis-memory.
_MANAGED = ("threads", "sessions", "programs", "goals", "candidates")


@dataclass
class VaultResult:
    written: int = 0
    pruned: int = 0
    dirs: list[str] = field(default_factory=list)

    def report(self) -> str:
        return (f"vault: wrote {self.written} note(s), pruned {self.pruned} stale; "
                f"root {config.vault_dir()}")


# --------------------------------------------------------------------------- #
# YAML frontmatter (small, dependency-free, valid for scalars + string lists)
# --------------------------------------------------------------------------- #
def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    return "'" + s.replace("'", "''") + "'"


def _yaml_list(items) -> str:
    return "[" + ", ".join(_yaml_scalar(x) for x in items) + "]"


def frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, (list, tuple)):
            lines.append(f"{k}: {_yaml_list(v)}")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _date(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def _slink(sid: str, written: set[str]) -> str:
    """A ``[[session-…]]`` wikilink only if that note exists; else plain text, so
    a link never dangles (stale candidate members, trivial sessions, …)."""
    return f"[[session-{sid}]]" if sid in written else f"session-{sid}"


def _artifact_lines(rec: dict) -> list[str]:
    arts = rec.get("artifacts") or {}
    out = []
    for pr in arts.get("prs", []):
        out.append(f"- PR {pr}")
    for br in arts.get("branches", []):
        out.append(f"- branch `{br}`")
    for url in arts.get("urls", [])[:5]:
        out.append(f"- {url}")
    return out


# --------------------------------------------------------------------------- #
# note bodies
# --------------------------------------------------------------------------- #
def _thread_note(t, parent: str | None, written: set[str]) -> str:
    fm = frontmatter({
        "slug": t.slug,
        "type": "thread",
        "parent": parent,
        "sessions_30d": t.sessions_in_window,
        "sessions_total": t.count,
        "last_active": _date(t.last_activity),
        "relevance": round(t.relevance, 4),
        "dormant": t.dormant,
        "closed": t.closed,
    })
    body = [f"# {t.slug}", ""]
    if parent:
        body.append(f"Parent: [[{parent}]]")
        body.append("")
    if t.status_line:
        body.append(f"> {t.status_line}")
        body.append("")
    if t.sessions:
        latest = t.sessions[0]
        body.append("## Latest activity")
        body.append("")
        body.append(f"**{latest.get('title','')}**")
        body.append("")
        if latest.get("summary"):
            body.append(latest["summary"])
            body.append("")
    body.append("## Sessions")
    body.append("")
    for rec in t.sessions:
        body.append(f"- {_slink(rec['session_id'], written)}")
    body.append("")
    return fm + "\n" + "\n".join(body)


def _session_note(rec: dict, slug: str | None) -> str:
    ts = dashboard._parse_ts(rec.get("t_end")) or dashboard._parse_ts(rec.get("t_start"))
    fm = frontmatter({
        "session": rec["session_id"],
        "type": "session",
        "date": _date(ts),
        "cwd": rec.get("cwd", ""),
        "thread": slug,
        "status_signals": rec.get("status_signals") or [],
    })
    body = [f"# {rec.get('title','(session)')}", ""]
    if slug:
        body.append(f"Thread: [[{slug}]]")
        body.append("")
    if rec.get("summary"):
        body.append(rec["summary"])
        body.append("")
    arts = _artifact_lines(rec)
    if arts:
        body.append("## Artifacts")
        body.append("")
        body.extend(arts)
        body.append("")
    return fm + "\n" + "\n".join(body)


def _empty_thread_note(node, parent: str | None) -> str:
    """A registry thread named in the hierarchy but with no summarized sessions
    yet — written so a parent's ``[[slug]]`` child link still resolves."""
    fm = frontmatter({
        "slug": node.slug, "type": "thread", "parent": parent,
        "sessions_30d": 0, "sessions_total": 0, "last_active": None,
        "relevance": round(node.relevance, 4), "dormant": False, "closed": False,
    })
    body = [f"# {node.slug}", ""]
    if parent:
        body += [f"Parent: [[{parent}]]", ""]
    body += ["_no summarized sessions in the window yet_", ""]
    return fm + "\n" + "\n".join(body)


def _node_note(node, kind: str) -> str:
    fm = frontmatter({
        "slug": node.slug,
        "type": kind,
        "title": node.title or node.slug,
        "sessions": node.sessions,
        "sessions_30d": node.sessions_in_window,
        "last_active": _date(node.last_activity),
        "relevance": round(node.relevance, 4),
    })
    body = [f"# {node.title or node.slug}", "",
            f"_{kind} — agent-drafted roll-up, standing until Daniel edits_", ""]
    if node.note:
        body.append(node.note)
        body.append("")
    body.append("## Children")
    body.append("")
    for c in node.children:
        body.append(f"- [[{c.slug}]] — {c.sessions} session(s), rel {c.relevance:.2f}")
    body.append("")
    return fm + "\n" + "\n".join(body)


def _candidate_note(c: dict, written: set[str]) -> str:
    fm = frontmatter({
        "slug": c["slug"],
        "type": "candidate",
        "name": c["name"],
        "members": len(c["members"]),
    })
    body = [f"# {c['name']}", "",
            "_agent-drafted candidate, standing until Daniel promotes or deletes_", "",
            "## Member sessions", ""]
    for m in c["members"]:
        body.append(f"- {_slink(m, written)}")
    body.append("")
    return fm + "\n" + "\n".join(body)


def _index_note(dash: dashboard.Dashboard, written: set[str]) -> str:
    top = sorted(dash.threads, key=lambda t: t.relevance, reverse=True)[:15]
    dormant = [t for t in dash.threads if t.dormant]
    L = ["# threads — vault index", "",
         f"_generated {dash.generated_at.isoformat()} · "
         f"{len(dash.threads)} thread(s), match rate "
         f"{dash.match_rate * 100:.0f}%_", "",
         "## Top threads by relevance", ""]
    for t in top:
        last = _date(t.last_activity) or "?"
        L.append(f"1. [[{t.slug}]] — rel **{t.relevance:.2f}**, "
                 f"{t.sessions_in_window} in 30d, last {last}")
    L += ["", "## Dormant threads", ""]
    if dormant:
        for t in dormant:
            L.append(f"- [[{t.slug}]] — last {_date(t.last_activity) or '?'}")
    else:
        L.append("_none_")
    cov = dash.coverage()
    L += ["", "## Coverage", "",
          f"- goals with no active descendant: "
          f"{', '.join(cov['goals_no_active']) or 'none'}",
          f"- threads under no goal: "
          f"{', '.join(f'[[{s}]]' for s in cov['threads_no_goal']) or 'none'}",
          "", "## Unfiled inbox", ""]
    if dash.unfiled:
        for rec in dash.unfiled:
            ts = dashboard._parse_ts(rec.get("t_end"))
            when = ts.date().isoformat() if ts else "?"
            L.append(f"- **{when}** — {rec.get('title','')[:80]} "
                     f"({_slink(rec['session_id'], written)})")
    else:
        L.append("_none_")
    L += ["", "## Candidate threads", ""]
    if dash.candidates:
        for c in dash.candidates:
            L.append(f"- [[{c['slug']}]] — {len(c['members'])} session(s)")
    else:
        L.append("_none_")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# regeneration
# --------------------------------------------------------------------------- #
def _iter_nodes(forest):
    for root in forest:
        yield from _walk(root)


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def write_vault(dash: dashboard.Dashboard | None = None, *,
                now: datetime | None = None) -> VaultResult:
    dash = dash or dashboard.build(now=now)
    root = config.vault_dir()
    for sub in _MANAGED:
        (root / sub).mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)

    # expected files per managed subdir → prune anything else afterwards.
    expected: dict[str, set[str]] = {s: set() for s in _MANAGED}
    res = VaultResult(dirs=[str(root / s) for s in _MANAGED])

    parent_of = dash.hierarchy.parent_of if dash.hierarchy else {}

    # sessions first — a note per non-trivial summary; matched slug from
    # assignments. `written` is the set of resolvable session-link targets.
    slug_by_session = {a["session_id"]: a.get("slug")
                       for a in spool.load_assignments()}
    written: set[str] = set()
    for rec in spool.load_all_summaries():
        if rec.get("trivial"):
            continue
        sid = rec["session_id"]
        name = f"session-{sid}.md"
        (root / "sessions" / name).write_text(
            _session_note(rec, slug_by_session.get(sid)))
        expected["sessions"].add(name)
        written.add(sid)
        res.written += 1

    # threads
    for t in dash.threads:
        name = f"{t.slug}.md"
        (root / "threads" / name).write_text(
            _thread_note(t, parent_of.get(t.slug), written))
        expected["threads"].add(name)
        res.written += 1

    # hierarchy nodes (programs + goals); plus any thread named in the hierarchy
    # that had no summarized sessions (so parent child-links still resolve).
    for node in _iter_nodes(dash.forest):
        if node.kind == "program":
            name = f"{node.slug}.md"
            (root / "programs" / name).write_text(_node_note(node, "program"))
            expected["programs"].add(name)
            res.written += 1
        elif node.kind == "goal":
            name = f"{node.slug}.md"
            (root / "goals" / name).write_text(_node_note(node, "goal"))
            expected["goals"].add(name)
            res.written += 1
        elif node.kind == "thread" and f"{node.slug}.md" not in expected["threads"]:
            name = f"{node.slug}.md"
            (root / "threads" / name).write_text(
                _empty_thread_note(node, parent_of.get(node.slug)))
            expected["threads"].add(name)
            res.written += 1

    # candidates
    for c in dash.candidates:
        name = f"{c['slug']}.md"
        (root / "candidates" / name).write_text(_candidate_note(c, written))
        expected["candidates"].add(name)
        res.written += 1

    # index
    (root / "INDEX.md").write_text(_index_note(dash, written))
    res.written += 1

    # prune stale notes whose source vanished (idempotent regeneration)
    for sub in _MANAGED:
        d = root / sub
        for p in d.glob("*.md"):
            if p.name not in expected[sub]:
                try:
                    p.unlink()
                    res.pruned += 1
                except OSError:
                    pass
    return res
