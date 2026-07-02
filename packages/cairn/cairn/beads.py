"""Import issues from a Beads (`bd`) export into a cairn store.

Beads writes a stable `.beads/issues.jsonl` — one JSON issue per line. This
maps those records onto cairn's model, **preserving the original Beads ids**
(e.g. ``stg-teu.2``) so parent/blocked-by edges survive the move. See the
field-mapping table in dtch1997/cairn#1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import MAX_PRIORITY, Issue, Status, now
from .store import CairnError, Store

# Beads status -> cairn status. Beads' `blocked` is derived in cairn from open
# dependencies rather than stored, so it collapses to `open` on import.
_STATUS_MAP = {
    "open": Status.OPEN.value,
    "in_progress": Status.IN_PROGRESS.value,
    "blocked": Status.OPEN.value,
    "closed": Status.CLOSED.value,
}


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    ids: list = field(default_factory=list)


def _compose_description(rec: dict) -> str:
    """Fold Beads' acceptance_criteria + notes into cairn's single description."""
    parts = [(rec.get("description") or "").strip()]
    ac = (rec.get("acceptance_criteria") or "").strip()
    if ac:
        parts.append(f"## Acceptance criteria\n{ac}")
    notes = (rec.get("notes") or "").strip()
    if notes:
        parts.append(f"## Notes\n{notes}")
    return "\n\n".join(p for p in parts if p).strip()


def beads_record_to_issue(rec: dict) -> Issue | None:
    """Map one Beads issue record to a cairn Issue, or None if it's not an issue."""
    if rec.get("_type", "issue") != "issue":
        return None
    iid = rec.get("id")
    if not iid:
        return None

    parent = None
    blocked_by: list[str] = []
    for dep in rec.get("dependencies") or []:
        target = dep.get("depends_on_id")
        if not target:
            continue
        dtype = dep.get("type")
        if dtype == "parent-child":
            parent = target
        elif dtype == "blocks":
            blocked_by.append(target)
        # other dep types (related, discovered-from, ...) are intentionally dropped

    try:
        priority = min(int(rec.get("priority", 2)), MAX_PRIORITY)
    except (TypeError, ValueError):
        priority = 2

    status = _STATUS_MAP.get(rec.get("status", "open"), Status.OPEN.value)
    created = rec.get("created_at") or now()
    return Issue(
        id=iid,
        title=rec.get("title") or iid,
        description=_compose_description(rec),
        status=status,
        priority=priority,
        type=rec.get("issue_type") or "task",
        assignee=rec.get("owner") or None,
        blocked_by=blocked_by,
        parent=parent,
        created=created,
        updated=rec.get("updated_at") or created,
        closed_at=rec.get("closed_at") if status == Status.CLOSED.value else None,
    )


def import_beads(store: Store, path: str | Path, *, skip_existing: bool = False) -> ImportResult:
    """Import every issue from a Beads ``issues.jsonl`` into ``store``.

    By default an imported id overwrites an existing file of the same id (the
    expected one-shot-migration case); pass ``skip_existing=True`` to keep
    whatever is already there.
    """
    path = Path(path)
    if not path.exists():
        raise CairnError(f"beads export not found: {path}")

    result = ImportResult()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CairnError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
        issue = beads_record_to_issue(rec)
        if issue is None:
            continue
        if store.put(issue, overwrite=not skip_existing):
            result.imported += 1
            result.ids.append(issue.id)
        else:
            result.skipped += 1
    return result
