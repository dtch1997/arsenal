"""Thin wrappers around the ``gh`` CLI. All network I/O lives here so the
policy (lanes.py), sweep, and notes stay pure and offline-testable. Every
function degrades gracefully: a missing ``gh`` or an API error yields empty
data plus a human-readable warning — never an exception."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .lanes import PR

_PR_FIELDS = (
    "number,title,url,author,createdAt,isDraft,labels,files,"
    "reviewDecision,statusCheckRollup,headRefName,isCrossRepository"
)


def _gh(args: list[str], timeout: int = 60) -> tuple[str, str | None]:
    """Run gh, return (stdout, warning)."""
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return "", "gh CLI not found"
    except subprocess.TimeoutExpired:
        return "", f"gh timed out: {' '.join(args[:4])}"
    if proc.returncode != 0:
        return "", f"gh failed ({' '.join(args[:4])}): {proc.stderr.strip()[:200]}"
    return proc.stdout, None


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _norm_checks(rollup) -> str:
    if not rollup:
        return "none"
    states = {
        (c.get("conclusion") or c.get("state") or "").upper() for c in rollup
    }
    if states & {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
        return "failing"
    if states & {"", "PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", "WAITING"}:
        return "pending"
    return "passing"


def list_open_prs(repo: str) -> tuple[list[PR], list[str]]:
    out, warn = _gh(
        ["pr", "list", "-R", repo, "--state", "open", "--limit", "100",
         "--json", _PR_FIELDS]
    )
    if warn:
        return [], [f"{repo}: {warn}"]
    warnings: list[str] = []
    prs: list[PR] = []
    try:
        rows = json.loads(out)
    except json.JSONDecodeError as e:
        return [], [f"{repo}: bad gh output ({e})"]
    for row in rows:
        try:
            pr = PR(
                repo=repo,
                number=row["number"],
                title=row.get("title", ""),
                url=row.get("url", ""),
                author=(row.get("author") or {}).get("login", ""),
                created_at=_parse_ts(row["createdAt"]),
                is_draft=bool(row.get("isDraft")),
                labels=[l["name"] for l in row.get("labels") or []],
                files=[f["path"] for f in row.get("files") or []],
                checks=_norm_checks(row.get("statusCheckRollup")),
                review_decision=row.get("reviewDecision") or "",
                head_ref=row.get("headRefName", ""),
                cross_repo=bool(row.get("isCrossRepository")),
            )
        except (KeyError, ValueError) as e:
            warnings.append(f"{repo}: could not parse PR row ({e})")
            continue
        prs.append(pr)
    return prs, warnings


def list_merged_since(repo: str, since: datetime) -> tuple[list[dict], list[str]]:
    out, warn = _gh(
        ["pr", "list", "-R", repo, "--state", "merged", "--limit", "50",
         "--search", f"merged:>={since.date().isoformat()}",
         "--json", "number,title,url,mergedAt,author,labels"]
    )
    if warn:
        return [], [f"{repo}: {warn}"]
    try:
        rows = json.loads(out)
    except json.JSONDecodeError as e:
        return [], [f"{repo}: bad gh output ({e})"]
    merged = []
    for row in rows:
        ts = _parse_ts(row["mergedAt"])
        if ts < since:
            continue  # search granularity is per-day; trim to the window
        merged.append({
            "repo": repo,
            "number": row["number"],
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "merged_at": ts,
            "author": (row.get("author") or {}).get("login", ""),
            "labels": [l["name"] for l in row.get("labels") or []],
        })
    return merged, []


def merge_pr(pr: PR) -> str | None:
    """Squash-merge; returns a warning string on failure, None on success.

    Deliberately no ``--delete-branch`` (pinned-main convention: it half-fails
    when the branch is checked out in a worktree); the remote branch is deleted
    via the API instead, and local worktree cleanup stays with sessions.
    """
    _, warn = _gh(["pr", "merge", "-R", pr.repo, str(pr.number), "--squash"], timeout=120)
    if warn:
        return f"{pr.ref}: merge failed — {warn}"
    if pr.head_ref and not pr.cross_repo:
        _, del_warn = _gh(["api", "-X", "DELETE", f"repos/{pr.repo}/git/refs/heads/{pr.head_ref}"])
        if del_warn:
            return f"{pr.ref}: merged, but remote branch delete failed — {del_warn}"
    return None
