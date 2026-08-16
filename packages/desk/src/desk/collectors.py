"""Collectors — each surfaces things blocked on Daniel and returns
``(items, warnings)``. An item is ``{id, kind, title, age_days, link, detail}``.

Every collector degrades gracefully: a missing directory, missing ``gh``, or a
malformed file yields an empty list plus a human-readable warning (rendered in
the inbox footer) — never an exception.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import Config

MARKER_NEEDLES = ("blocked-on-daniel", "standing until daniel edits")


def _age_days(ts: datetime, now: datetime) -> float:
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def _mtime_age_days(path: Path, now: datetime) -> float:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return 0.0
    return _age_days(mtime, now)


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1. concierge — blocked/failed pool tasks
# --------------------------------------------------------------------------- #
def collect_concierge(cfg: Config, now: datetime) -> tuple[list[dict], list[str]]:
    root = cfg.concierge_home_path / "tasks"
    if not root.is_dir():
        return [], [f"concierge: {root} not found"]
    items, warnings = [], []
    for path in sorted(root.glob("t-*.json")):
        if path.name.endswith(".wait.json"):
            continue  # sidecar, not a task
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"concierge: could not read {path.name} ({e})")
            continue
        status = data.get("status")
        if status not in ("blocked", "failed"):
            continue
        tid = data.get("id") or path.stem
        title = data.get("title") or tid
        note = (data.get("blocked_note") or data.get("note")
                or data.get("error") or data.get("reason") or "")
        ts = _parse_ts(data.get("updated_at")) or _parse_ts(data.get("created_at"))
        age = _age_days(ts, now) if ts else _mtime_age_days(path, now)
        detail = f"[{status}] {note}".strip() if note else f"[{status}]"
        items.append({
            "id": f"concierge:{tid}",
            "kind": "concierge",
            "title": title,
            "age_days": round(age, 2),
            "link": str(path),
            "detail": detail,
        })
    return items, warnings


# --------------------------------------------------------------------------- #
# 2. prs — open PRs per configured repo (via `gh`)
# --------------------------------------------------------------------------- #
def _default_gh_runner(repo: str) -> list[dict]:
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--json",
         "number,title,createdAt,url"],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"gh exited {out.returncode}")
    return json.loads(out.stdout or "[]")


def collect_prs(cfg: Config, now: datetime, runner=None) -> tuple[list[dict], list[str]]:
    runner = runner or _default_gh_runner
    items, warnings = [], []
    for repo in cfg.github_repos:
        try:
            prs = runner(repo)
        except FileNotFoundError:
            warnings.append("prs: `gh` not found on PATH")
            continue
        except Exception as e:
            warnings.append(f"prs: {repo}: {e}")
            continue
        for pr in prs:
            ts = _parse_ts(pr.get("createdAt"))
            age = _age_days(ts, now) if ts else 0.0
            old = age >= cfg.pr_age_warn_days
            detail = f"open {age:.0f}d" + (" — OLD" if old else "")
            items.append({
                "id": f"pr:{repo}#{pr.get('number')}",
                "kind": "prs",
                "title": f"{repo}#{pr.get('number')}: {pr.get('title', '')}",
                "age_days": round(age, 2),
                "link": pr.get("url", ""),
                "detail": detail,
            })
    return items, warnings


# --------------------------------------------------------------------------- #
# 3. markers — grep marker files for BLOCKED-ON-DANIEL lines
# --------------------------------------------------------------------------- #
def collect_markers(cfg: Config, now: datetime) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    for base in cfg.marker_path_list:
        if not base.exists():
            warnings.append(f"markers: {base} not found")
            continue
        md_files = [base] if base.is_file() else sorted(base.rglob("*.md"))
        for path in md_files:
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError as e:
                warnings.append(f"markers: could not read {path} ({e})")
                continue
            age = _mtime_age_days(path, now)
            for lineno, line in enumerate(lines, start=1):
                low = line.lower()
                if any(needle in low for needle in MARKER_NEEDLES):
                    items.append({
                        "id": f"marker:{path}:{lineno}",
                        "kind": "markers",
                        "title": line.strip(),
                        "age_days": round(age, 2),
                        "link": f"{path}:{lineno}",
                        "detail": f"{path.name}:{lineno}",
                    })
    return items, warnings


# --------------------------------------------------------------------------- #
# 4. flares — recent warn/page flares from the spool
# --------------------------------------------------------------------------- #
def collect_flares(cfg: Config, now: datetime) -> tuple[list[dict], list[str]]:
    try:
        import flare
        log = flare.log_path()
    except Exception as e:  # pragma: no cover - flare is a hard dep
        return [], [f"flares: flare package unavailable ({e})"]
    if not log.exists():
        return [], []
    items, warnings = [], []
    for i, raw in enumerate(log.read_text().splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            warnings.append(f"flares: skipped malformed line {i + 1}")
            continue
        if rec.get("source") == "desk":
            continue  # desk's own notifications about inbox items — counting
            # them as items (and re-flaring them) is a feedback loop
        if rec.get("sev") not in ("warn", "page"):
            continue
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            continue
        age = _age_days(ts, now)
        if age > cfg.flare_stale_days:
            continue
        src = rec.get("source") or "?"
        items.append({
            "id": f"flare:{rec.get('ts')}:{rec.get('msg')}",
            "kind": "flares",
            "title": f"[{rec.get('sev')}] {rec.get('msg', '')}",
            "age_days": round(age, 2),
            "link": str(log),
            "detail": f"source {src} · host {rec.get('host', '?')}",
        })
    return items, warnings


COLLECTORS = {
    "concierge": collect_concierge,
    "prs": collect_prs,
    "markers": collect_markers,
    "flares": collect_flares,
}
