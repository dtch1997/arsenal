"""``threads weave`` — assign each summary to a thread (registry slug).

Deterministic passes run first, in priority order, using the hints extracted at
scan time (no transcript re-read, no model):

1. **concierge** — a ``concierge-home/workspaces/<tid>`` cwd → read the task
   JSON → map its repo/title/spec to a slug, else the ``unmatched-concierge``
   review bucket.
2. **branch / stub / goals** — a memory-stub or goals file edited, or a git
   branch, mapped to a slug.
3. **repo** — the dominant touched/referenced repo mapped to a slug.
4. **model-validated fallback** — the summarizer's ``candidate_slugs``, accepted
   only if the slug exists in the registry.

Unmatched → the unfiled inbox. Then ONE clustering call over the unfiled
summaries drafts candidate threads (>= 2 members) into ``candidates/``.

``weave(check=True)`` is the offline gate hook — no model, no network.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config, registry, spool, summarize

REVIEW_BUCKET = "unmatched-concierge"

# methods that count toward the deterministic-match rate gate
DETERMINISTIC = {"stub-edit", "goals", "repo", "branch"}

CONFIDENCE = {
    "concierge": 0.9, "concierge-review": 0.5,
    "stub-edit": 0.95, "goals": 0.9, "repo": 0.8, "branch": 0.85,
    "model": 0.6, "unfiled": 0.0,
}

_CONCIERGE_CWD = re.compile(r"concierge-home/workspaces/([A-Za-z0-9_-]+)")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _match_text_to_slug(reg: registry.Registry, text: str) -> str | None:
    """A slug whose name (or all its long tokens) appears in ``text``."""
    n = _norm(text)
    for slug in sorted(reg.slugs):
        if slug in n:
            return slug
    for slug in sorted(reg.slugs):
        tokens = [t for t in slug.split("-") if len(t) >= 4]
        if tokens and all(t in n for t in tokens):
            return slug
    return None


def _read_task(tid: str) -> dict | None:
    path = config.concierge_home() / "tasks" / f"{tid}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _concierge_slug(reg: registry.Registry, tid: str) -> str | None:
    task = _read_task(tid)
    if not task:
        return None
    ws = task.get("workspace") or {}
    repo = ws.get("repo")
    if isinstance(repo, str) and repo:
        slug = reg.repo_to_slug(repo.rstrip("/").split("/")[-1])
        if slug:
            return slug
    parts = [str(task.get("title") or "")]
    spec = task.get("spec")
    if isinstance(spec, str) and spec:
        spec_path = config.concierge_home() / spec
        if spec_path.exists():
            try:
                parts.append(spec_path.read_text(errors="replace"))
            except OSError:
                pass
    return _match_text_to_slug(reg, " ".join(parts))


def assign_one(rec: dict, reg: registry.Registry) -> tuple[str | None, str]:
    """Return ``(slug, method)`` for one summary record."""
    hints = rec.get("hints") or {}
    cwd = rec.get("cwd") or ""

    m = _CONCIERGE_CWD.search(cwd)
    if m:
        slug = _concierge_slug(reg, m.group(1))
        if slug and reg.has(slug):
            return slug, "concierge"
        return None, "concierge-review"

    for slug in hints.get("stubs", []):
        if reg.has(slug):
            return slug, "stub-edit"
    for slug in hints.get("goals", []):
        if reg.has(slug):
            return slug, "goals"
    for repo in hints.get("repos", []):
        slug = reg.repo_to_slug(repo)
        if slug:
            return slug, "repo"
    for branch in hints.get("branches", []):
        slug = reg.branch_to_slug(branch)
        if slug:
            return slug, "branch"
    for slug in rec.get("candidate_slugs", []):
        if reg.has(slug):
            return slug, "model"
    return None, "unfiled"


# --------------------------------------------------------------------------- #
# clustering the unfiled inbox into candidate threads
# --------------------------------------------------------------------------- #
_CLUSTER_HINT = """\
You are grouping unfiled Claude sessions into candidate threads. Return ONLY a
JSON object: {"clusters": [{"name": <short kebab slug>, "note": <one paragraph
distilling the shared thread>, "members": [<session_id>, ...]}]}. Only group
sessions that clearly share a topic/goal; every cluster MUST have >= 2 members;
leave loners out entirely.
"""


def build_cluster_prompt(unfiled: list[dict]) -> str:
    rows = []
    for rec in unfiled:
        kws = ", ".join(rec.get("keywords", [])[:8])
        rows.append(f"- {rec['session_id']}: {rec.get('title','')} [{kws}]")
    return _CLUSTER_HINT + "\nUNFILED SESSIONS:\n" + "\n".join(rows) + "\n"


def _slugify(name: str) -> str:
    return _norm(name) or "candidate"


def write_candidate(name: str, note: str, members: list[str]) -> Path:
    config.ensure_spool()
    path = config.candidates_dir() / f"{_slugify(name)}.md"
    body = [
        f"# {name}",
        "",
        "_agent-drafted, standing until Daniel edits_",
        "",
        note.strip(),
        "",
        f"**{len(members)} member session(s):**",
        *[f"- {m}" for m in members],
        "",
    ]
    path.write_text("\n".join(body))
    return path


def cluster_unfiled(unfiled: list[dict], *, runner=summarize.default_runner,
                    model: str = config.MODEL) -> list[dict]:
    """One model call; write >=2-member candidate files. Returns cluster dicts."""
    valid_ids = {r["session_id"] for r in unfiled}
    if len(unfiled) < 2:
        return []
    try:
        result = runner(build_cluster_prompt(unfiled), model=model)
        data = json.loads(summarize._strip_fence(result.get("text", "")))
    except Exception:
        return []
    clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(clusters, list):
        return []
    out = []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        members = [m for m in (c.get("members") or []) if m in valid_ids]
        if len(members) < 2:
            continue
        name = str(c.get("name") or "candidate")
        note = str(c.get("note") or "")
        write_candidate(name, note, members)
        out.append({"name": name, "note": note, "members": members})
    return out


# --------------------------------------------------------------------------- #
# the weave itself
# --------------------------------------------------------------------------- #
@dataclass
class WeaveResult:
    assignments: list[dict] = field(default_factory=list)
    method_counts: Counter = field(default_factory=Counter)
    candidates: list[dict] = field(default_factory=list)
    matched: int = 0
    total: int = 0

    @property
    def match_rate(self) -> float:
        return self.matched / self.total if self.total else 0.0

    def report(self) -> str:
        lines = [
            f"weave: {self.matched}/{self.total} sessions matched to a thread "
            f"({self.match_rate * 100:.0f}%)",
            "  by method: " + ", ".join(
                f"{m}={n}" for m, n in sorted(self.method_counts.items())),
            f"  candidate threads drafted: {len(self.candidates)}",
        ]
        return "\n".join(lines)


def weave(*, runner=summarize.default_runner, model: str = config.MODEL,
          cluster: bool = True) -> WeaveResult:
    reg = registry.load_registry()
    summaries = spool.load_all_summaries()
    res = WeaveResult(total=len(summaries))
    rows = []
    unfiled = []
    for rec in summaries:
        slug, method = assign_one(rec, reg)
        rows.append({
            "session_id": rec["session_id"],
            "slug": slug,
            "method": method,
            "confidence": CONFIDENCE.get(method, 0.0),
        })
        res.method_counts[method] += 1
        if slug is not None:
            res.matched += 1
        elif method == "unfiled" and not rec.get("trivial"):
            unfiled.append(rec)
    spool.write_assignments(rows)
    res.assignments = rows
    if cluster and unfiled:
        res.candidates = cluster_unfiled(unfiled, runner=runner, model=model)
    return res


def weave_check() -> tuple[bool, str]:
    """Offline gate: assignments cover all summaries, the deterministic-match
    rate over non-trivial non-concierge sessions is >= 70%, and every concierge
    session is resolved (to a slug or the review bucket)."""
    summaries = spool.load_all_summaries()
    assignments = {a["session_id"]: a for a in spool.load_assignments()}
    if not summaries:
        return False, "no summaries in the spool (run scan first)"

    problems = []
    det_num = det_den = 0
    conc_total = conc_resolved = 0
    for rec in summaries:
        sid = rec["session_id"]
        a = assignments.get(sid)
        if a is None:
            problems.append(f"no assignment for {sid}")
            continue
        method = a.get("method")
        is_conc = bool(rec.get("is_concierge")) or method in (
            "concierge", "concierge-review")
        if is_conc:
            conc_total += 1
            if a.get("slug") is not None or method == "concierge-review":
                conc_resolved += 1
            else:
                problems.append(f"concierge session unresolved: {sid}")
            continue
        if rec.get("trivial"):
            continue
        det_den += 1
        if method in DETERMINISTIC and a.get("slug") is not None:
            det_num += 1

    det_rate = det_num / det_den if det_den else 1.0
    conc_rate = conc_resolved / conc_total if conc_total else 1.0
    lines = [
        f"deterministic match rate (non-trivial, non-concierge): "
        f"{det_num}/{det_den} = {det_rate * 100:.0f}% (need >= 70%)",
        f"concierge sessions resolved: {conc_resolved}/{conc_total} "
        f"= {conc_rate * 100:.0f}% (need 100%)",
    ]
    ok = not problems and det_rate >= 0.70 and conc_rate >= 1.0
    if problems:
        lines.append(f"problems: {len(problems)}")
        lines.extend("  - " + p for p in problems[:20])
    status = "weave --check OK" if ok else "weave --check FAIL"
    return ok, status + "\n" + "\n".join(lines)
