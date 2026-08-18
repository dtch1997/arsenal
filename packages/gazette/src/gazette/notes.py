"""Patch notes — the morning artifact. Pure rendering over data collected by
gh.py, so it's testable offline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config, spool_dir
from .lanes import PR, Lane, decide


def notes_dir():
    return spool_dir() / "notes"


def _lane_of(labels: list[str]) -> str:
    for l in labels:
        if l.startswith("lane:"):
            return l.split(":", 1)[1]
    return "?"


def build_notes(
    cfg: Config,
    now: datetime,
    open_prs: list[PR],
    merged: list[dict],
    warnings: list[str] | None = None,
) -> str:
    """Render the patch-notes markdown from normalized data."""
    lines = [f"# Patch notes — {now.date().isoformat()}", ""]

    lines.append(f"## Merged (last {cfg.notes_window_hours}h)")
    if merged:
        for m in sorted(merged, key=lambda m: m["merged_at"]):
            repo_short = m["repo"].split("/")[-1]
            lines.append(
                f"- {repo_short}#{m['number']} **{m['title']}** "
                f"({_lane_of(m['labels'])}) — {m['url']}"
            )
    else:
        lines.append("- nothing merged")
    lines.append("")

    window, blocked, anomalies = [], [], []
    for pr in open_prs:
        d = decide(pr, cfg, now)
        for a in d.anomalies:
            anomalies.append(f"{pr.ref}: {a}")
        if d.lane is Lane.BLOCKED or d.action == "skip":
            blocked.append((pr, d))
        elif d.action in ("wait", "merge"):
            window.append((pr, d))

    lines.append("## In the pipeline (merges unless vetoed)")
    if window:
        for pr, d in sorted(window, key=lambda t: t[0].created_at):
            files = ", ".join(pr.files[:5]) + (" …" if len(pr.files) > 5 else "")
            lines.append(f"- {pr.ref} **{pr.title}** ({d.lane.value}) — {d.reason}")
            lines.append(f"  - files: {files or '(none listed)'} — veto: add `veto` label or request changes — {pr.url}")
    else:
        lines.append("- queue is empty")
    lines.append("")

    lines.append("## Waiting on you")
    if blocked:
        for pr, d in sorted(blocked, key=lambda t: t[0].created_at):
            lines.append(f"- {pr.ref} **{pr.title}** — {d.reason} — {pr.url}")
    else:
        lines.append("- nothing")
    lines.append("")

    if anomalies:
        lines.append("## Anomalies")
        lines.extend(f"- {a}" for a in anomalies)
        lines.append("")
    if warnings:
        lines.append("## Collector warnings")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")
    return "\n".join(lines)


def digest(cfg: Config, now: datetime, open_prs: list[PR], merged: list[dict]) -> str:
    """One-paragraph summary for flare/Slack."""
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
