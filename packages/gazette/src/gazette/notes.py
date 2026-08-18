"""Patch notes — the morning artifact. Pure rendering over data collected by
gh.py, so it's testable offline.

Deadline-first layout: the edition opens with **Needs you** (the only section
where reading has consequences, each item with its default outcome), then
anomalies (trust calibration — present only when non-empty), then the news.
The ideal quiet morning is a one-glance close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import Config, spool_dir
from .lanes import PR, Lane, decide


def notes_dir():
    return spool_dir() / "notes"


def _veto_cmd(pr: PR) -> str:
    return f"`gh pr edit {pr.number} -R {pr.repo} --add-label veto`"


def _age_days(pr: PR, now: datetime) -> int:
    return max(0, (now - pr.created_at).days)


@dataclass
class Edition:
    """One morning's compiled state, ready to render as markdown or a flare."""

    now: datetime
    needs_you: list[str] = field(default_factory=list)  # full markdown lines
    needs_you_short: list[str] = field(default_factory=list)  # flare lines
    pipeline: list[str] = field(default_factory=list)  # ambient: nothing to do
    anomalies: list[str] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    visible_refs: list[str] = field(default_factory=list)  # for the edition log
    warnings: list[str] = field(default_factory=list)


def compile_edition(
    cfg: Config,
    now: datetime,
    open_prs: list[PR],
    merged: list[dict],
    appearances: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> Edition:
    ed = Edition(now=now, merged=sorted(merged, key=lambda m: m["merged_at"]),
                 warnings=list(warnings or []))
    for pr in sorted(open_prs, key=lambda p: p.created_at):
        seen = appearances.get(pr.ref, 0) if appearances is not None else None
        d = decide(pr, cfg, now, seen)
        ed.anomalies.extend(f"{pr.ref}: {a}" for a in d.anomalies)
        if d.reason == "draft":
            continue  # not being asked to merge yet — not part of the edition
        ed.visible_refs.append(pr.ref)
        if d.reason.startswith("vetoed"):
            ed.pipeline.append(f"- {pr.ref} **{pr.title}** ({d.lane.value}) — {d.reason} — {pr.url}")
        elif d.lane is Lane.BLOCKED:
            age = _age_days(pr, now)
            ed.needs_you.append(
                f"- {pr.ref} **{pr.title}** — blocked lane, {age}d old — "
                f"sits until you act — {pr.url}"
            )
            ed.needs_you_short.append(f"• blocked: {pr.ref} {pr.title} — {age}d old")
        elif d.lane is Lane.DELAY:
            if d.reason.startswith("checks"):
                # not mergeable until checks go green — ambient, and failing
                # checks already surface as an anomaly
                ed.pipeline.append(
                    f"- {pr.ref} **{pr.title}** ({d.lane.value}) — {d.reason} — {pr.url}"
                )
                continue
            if d.action == "merge":
                outcome = "merges at tonight's sweep unless vetoed"
            else:
                outcome = f"{d.reason}; merges once it elapses unless vetoed"
            ed.needs_you.append(
                f"- {pr.ref} **{pr.title}** — {outcome} — veto: {_veto_cmd(pr)} — {pr.url}"
            )
            when = "tonight" if d.action == "merge" else "after the window"
            ed.needs_you_short.append(
                f"• veto window: {pr.ref} {pr.title} — merges {when} unless vetoed"
            )
        else:  # auto lane waiting on checks / merging tonight — ambient
            ed.pipeline.append(f"- {pr.ref} **{pr.title}** ({d.lane.value}) — {d.reason} — {pr.url}")
    return ed


def build_notes(
    cfg: Config,
    ed: Edition,
    news: str | None = None,
    desk_text: str | None = None,
) -> str:
    """Render the full edition (the spooled markdown page)."""
    lines = [f"# Patch notes — {ed.now.date().isoformat()}", ""]

    lines.append(f"## Needs you ({len(ed.needs_you)})")
    if ed.needs_you:
        lines.extend(ed.needs_you)
    else:
        lines.append("- nothing — nothing waits on you today")
    if desk_text:
        lines += ["", "From the desk:", "", "```", desk_text.rstrip(), "```"]
    lines.append("")

    if ed.anomalies:
        lines.append("## Anomalies")
        lines.extend(f"- {a}" for a in ed.anomalies)
        lines.append("")

    lines.append(f"## News — merged (last {cfg.notes_window_hours}h)")
    if news:
        lines += [news.rstrip(), "", "### All merges"]
    if ed.merged:
        for m in ed.merged:
            repo_short = m["repo"].split("/")[-1]
            lines.append(f"- {repo_short}#{m['number']} **{m['title']}** — {m['url']}")
    else:
        lines.append("- nothing merged")
    lines.append("")

    if ed.pipeline:
        lines.append("## In the pipeline (nothing to do)")
        lines.extend(ed.pipeline)
        lines.append("")

    if ed.warnings:
        lines.append("## Collector warnings")
        lines.extend(f"- {w}" for w in ed.warnings)
        lines.append("")
    return "\n".join(lines)


def flare_body(ed: Edition, news: str | None = None, spool_path=None) -> str:
    """The morning Slack message: needs-you + anomalies + news TL;DR."""
    date = ed.now.date().isoformat()
    quiet = not ed.needs_you and not ed.anomalies
    lines: list[str] = []
    if quiet:
        lines.append(f"☀️ Patch notes {date} — quiet: {len(ed.merged)} merged, nothing needs you.")
    else:
        lines.append(f"☀️ Patch notes — {date}")
        lines.append("")
        lines.append(f"NEEDS YOU ({len(ed.needs_you)})")
        lines.extend(ed.needs_you_short or ["• nothing"])
        if ed.anomalies:
            lines.append("")
            lines.append(f"ANOMALIES ({len(ed.anomalies)})")
            lines.extend(f"• {a}" for a in ed.anomalies)
    if news:
        lines.append("")
        lines.append("NEWS")
        lines.append(news.rstrip())
    elif ed.merged and not quiet:
        refs = [f"{m['repo'].split('/')[-1]}#{m['number']}" for m in ed.merged]
        shown = ", ".join(refs[:8]) + (f" (+{len(refs) - 8} more)" if len(refs) > 8 else "")
        lines.append("")
        lines.append(f"NEWS: {len(refs)} merged — {shown}")
    if spool_path:
        lines.append("")
        lines.append(f"Full edition → {spool_path}")
    return "\n".join(lines)


def digest(cfg: Config, now: datetime, open_prs: list[PR], merged: list[dict]) -> str:
    """One-paragraph summary for ``gazette status``."""
    merged_refs = [f"{m['repo'].split('/')[-1]}#{m['number']}" for m in merged]
    waiting = blocked = 0
    for pr in open_prs:
        d = decide(pr, cfg, now)
        if d.lane is Lane.BLOCKED or d.action == "skip":
            blocked += 1
        else:
            waiting += 1
    parts = [f"patch notes {now.date().isoformat()}:"]
    parts.append(f"merged {len(merged)}" + (f" ({', '.join(merged_refs[:6])})" if merged_refs else ""))
    parts.append(f"{waiting} in pipeline")
    parts.append(f"{blocked} waiting on you")
    return " — ".join(parts)


def spool_notes(now: datetime, text: str):
    path = notes_dir() / f"{now.date().isoformat()}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError:
        return None
    return path
