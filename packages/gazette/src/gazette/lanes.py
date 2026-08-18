"""Lane resolution and merge decisions — pure functions, no I/O.

A PR's *lane* comes from its ``lane:*`` label, then gets demoted by what the
PR actually touches: auto-lane PRs touching protected paths drop to delay;
any PR touching credential-like paths drops to blocked. The label is the
author's claim; the demotion rules are the backstop that makes a wrong claim
harmless. Unlabeled PRs default to delay and are flagged as unclassified.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from .config import Config

LABEL_AUTO = "lane:auto"
LABEL_DELAY = "lane:delay"
LABEL_BLOCKED = "lane:blocked"
LABEL_VETO = "veto"

LANE_LABELS = (LABEL_AUTO, LABEL_DELAY, LABEL_BLOCKED)


class Lane(str, Enum):
    AUTO = "auto"
    DELAY = "delay"
    BLOCKED = "blocked"


@dataclass
class PR:
    """Normalized view of an open PR (see gh.list_open_prs)."""

    repo: str
    number: int
    title: str
    url: str
    author: str
    created_at: datetime
    is_draft: bool = False
    labels: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    checks: str = "none"  # none | passing | pending | failing
    review_decision: str = ""  # "" | APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED
    head_ref: str = ""
    cross_repo: bool = False

    @property
    def ref(self) -> str:
        return f"{self.repo.split('/')[-1]}#{self.number}"


def path_matches(path: str, glob: str) -> bool:
    """fnmatch with two conveniences: ``dir/**`` matches everything under dir,
    and a bare filename glob (no ``/``) matches at any depth (so ``CLAUDE.md``
    protects nested copies too)."""
    if glob.endswith("/**"):
        prefix = glob[:-2]  # keep trailing slash
        return path == glob[:-3] or path.startswith(prefix)
    if fnmatch.fnmatch(path, glob):
        return True
    if glob.startswith("**/") and fnmatch.fnmatch(path, glob[3:]):
        return True  # "**/x" also means a top-level "x"
    if "/" not in glob:
        return fnmatch.fnmatch(PurePosixPath(path).name, glob)
    return False


def _touched(files: list[str], globs: list[str]) -> list[str]:
    return [f for f in files if any(path_matches(f, g) for g in globs)]


def resolve_lane(labels: list[str], files: list[str], cfg: Config) -> tuple[Lane, list[str]]:
    """Return (lane, reasons). Reasons record demotions/defaults for the notes."""
    reasons: list[str] = []
    hot = _touched(files, cfg.blocked_globs)
    if hot:
        if LABEL_BLOCKED not in labels:
            reasons.append(f"demoted to blocked: touches {', '.join(hot[:3])}")
        return Lane.BLOCKED, reasons
    if LABEL_BLOCKED in labels:
        return Lane.BLOCKED, reasons
    if LABEL_AUTO in labels:
        protected = _touched(files, cfg.protected_globs)
        if protected:
            reasons.append(f"demoted to delay: touches {', '.join(protected[:3])}")
            return Lane.DELAY, reasons
        return Lane.AUTO, reasons
    if LABEL_DELAY in labels:
        return Lane.DELAY, reasons
    reasons.append("unclassified (no lane:* label) — defaulting to delay")
    return Lane.DELAY, reasons


@dataclass
class Decision:
    action: str  # merge | wait | skip
    lane: Lane
    reason: str
    anomalies: list[str] = field(default_factory=list)


def decide(pr: PR, cfg: Config, now: datetime, appearances: int | None = None) -> Decision:
    """The sweep's whole policy, as one pure function over a normalized PR.

    ``appearances`` is how many distinct morning editions the PR has appeared
    in (see editions.py). When provided, the delay-lane veto window is counted
    in delivered editions — a skipped morning pauses the window — and the
    wall-clock ``delay_hours`` is kept only as a stall detector. When None
    (no edition data supplied), falls back to the wall-clock window.
    """
    lane, reasons = resolve_lane(pr.labels, pr.files, cfg)
    anomalies = list(reasons)
    if pr.is_draft:
        return Decision("skip", lane, "draft", anomalies)
    if LABEL_VETO in pr.labels:
        return Decision("skip", lane, "vetoed (`veto` label)", anomalies)
    if pr.review_decision == "CHANGES_REQUESTED":
        return Decision("skip", lane, "vetoed (changes requested)", anomalies)
    if lane is Lane.BLOCKED:
        return Decision("skip", lane, "blocked lane — needs Daniel", anomalies)
    if pr.checks == "failing":
        anomalies.append("checks failing")
        return Decision("wait", lane, "checks failing", anomalies)
    if pr.checks == "pending":
        return Decision("wait", lane, "checks pending", anomalies)
    if lane is Lane.AUTO:
        return Decision("merge", lane, "auto lane, checks green", anomalies)
    age_h = (now - pr.created_at).total_seconds() / 3600.0
    if appearances is None:
        if age_h >= cfg.delay_hours:
            return Decision("merge", lane, f"veto window elapsed ({age_h:.0f}h)", anomalies)
        remaining = cfg.delay_hours - age_h
        return Decision("wait", lane, f"in veto window (~{remaining:.0f}h left)", anomalies)
    if appearances >= cfg.delay_editions:
        return Decision(
            "merge", lane,
            f"veto window elapsed (seen in {appearances}/{cfg.delay_editions} morning editions)",
            anomalies,
        )
    if age_h >= 3 * cfg.delay_hours:
        anomalies.append(
            f"stalled: aged {age_h:.0f}h but seen in only {appearances}/"
            f"{cfg.delay_editions} editions — is the notes cron running?"
        )
    return Decision(
        "wait", lane,
        f"in veto window (seen in {appearances}/{cfg.delay_editions} morning editions)",
        anomalies,
    )
