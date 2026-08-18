"""The nightly sweep: decide every open PR, merge what the lanes allow,
record everything to the spool (``~/.gazette/log.jsonl``) for the notes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import gh
from .config import Config, spool_dir
from .editions import load_appearances
from .lanes import PR, Decision, decide


def log_path():
    return spool_dir() / "log.jsonl"


def _record(entry: dict) -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def run(cfg: Config, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Returns a report: {merged, waiting, skipped, errors, warnings}."""
    now = now or datetime.now(timezone.utc)
    report: dict = {"merged": [], "waiting": [], "skipped": [], "errors": [], "warnings": []}
    appearances = load_appearances()  # delay windows count delivered editions
    for repo in cfg.github_repos:
        prs, warnings = gh.list_open_prs(repo)
        report["warnings"].extend(warnings)
        for pr in prs:
            decision = decide(pr, cfg, now, appearances.get(pr.ref, 0))
            row = {
                "ts": now.isoformat(),
                "repo": repo,
                "number": pr.number,
                "ref": pr.ref,
                "title": pr.title,
                "url": pr.url,
                "lane": decision.lane.value,
                "action": decision.action,
                "reason": decision.reason,
                "anomalies": decision.anomalies,
                "dry_run": dry_run,
            }
            if decision.action == "merge" and not dry_run:
                err = gh.merge_pr(pr)
                if err:
                    row["action"] = "error"
                    row["reason"] = err
                    report["errors"].append(row)
                else:
                    report["merged"].append(row)
            elif decision.action == "merge":
                row["action"] = "would-merge"
                report["merged"].append(row)
            elif decision.action == "wait":
                report["waiting"].append(row)
            else:
                report["skipped"].append(row)
            _record(row)
    return report


def format_report(report: dict) -> str:
    lines = []
    for key in ("merged", "waiting", "skipped", "errors"):
        for row in report[key]:
            lines.append(f"[{row['action']}] {row['ref']} ({row['lane']}) — {row['reason']}")
    for w in report["warnings"]:
        lines.append(f"[warn] {w}")
    return "\n".join(lines) if lines else "nothing to do"
