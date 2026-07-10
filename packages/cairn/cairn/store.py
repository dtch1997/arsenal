"""The store: one JSON file per issue under ``.cairn/``, plus agent memory.

Storage layout (all under the repo root ``.cairn/``):

    .cairn/config.json        # {"prefix": "cn"}
    .cairn/issues/<id>.json   # one file per issue — this is why branches merge
    .cairn/memory.jsonl       # append-only notes surfaced by `prime`

Writes are atomic (write-temp-then-rename) so a crash never leaves a half
file, and file-per-issue means two agents editing *different* issues never
touch the same file — git merges them with no conflict. Two agents editing
the *same* issue is last-write-wins locally, or a single-file git conflict
across branches (rare, and easy to resolve by hand).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Issue, Status, gen_id, now

DIRNAME = ".cairn"
DEFAULT_PREFIX = "cn"


class CairnError(Exception):
    pass


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (or cwd) to the nearest dir containing ``.cairn/``."""
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / DIRNAME).is_dir():
            return d
    return None


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.dir = self.root / DIRNAME
        self.issues_dir = self.dir / "issues"
        self.config_path = self.dir / "config.json"
        self.memory_path = self.dir / "memory.jsonl"

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def init(cls, root: Path, prefix: str = DEFAULT_PREFIX) -> "Store":
        store = cls(root)
        if store.dir.exists():
            raise CairnError(f"{DIRNAME}/ already exists at {store.root}")
        store.issues_dir.mkdir(parents=True)
        store._write_json(store.config_path, {"prefix": prefix})
        return store

    @classmethod
    def discover(cls, start: Path | None = None) -> "Store":
        root = find_root(start)
        if root is None:
            raise CairnError(
                f"no {DIRNAME}/ found here or in any parent directory "
                f"(run `cairn init` first)"
            )
        return cls(root)

    @property
    def prefix(self) -> str:
        cfg = self._read_json(self.config_path, default={}) or {}
        return cfg.get("prefix", DEFAULT_PREFIX)

    # ---- io helpers ------------------------------------------------------
    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)  # atomic on POSIX and Windows

    @staticmethod
    def _read_json(path: Path, default=None):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _issue_path(self, issue_id: str) -> Path:
        return self.issues_dir / f"{issue_id}.json"

    # ---- issue crud ------------------------------------------------------
    def create(
        self,
        title: str,
        description: str = "",
        priority: int = 2,
        type: str = "task",
        assignee: str | None = None,
        labels: list | None = None,
    ) -> Issue:
        iid = None
        for _ in range(16):
            candidate = gen_id(self.prefix, title)
            if not self._issue_path(candidate).exists():
                iid = candidate
                break
        if iid is None:
            raise CairnError("could not allocate a unique id — try again")
        issue = Issue(
            id=iid,
            title=title,
            description=description,
            priority=priority,
            type=type,
            assignee=assignee,
            labels=list(labels or []),
        )
        self._save(issue)
        return issue

    def _save(self, issue: Issue) -> None:
        self._write_json(self._issue_path(issue.id), issue.to_dict())

    def put(self, issue: Issue, *, overwrite: bool = True) -> bool:
        """Write an issue verbatim, id and all (used by import).

        Unlike ``create``, this preserves ``issue.id`` rather than minting one,
        so external ids survive a migration. Returns True if written, False if
        ``overwrite`` is False and an issue with that id already exists.
        """
        if not overwrite and self.exists(issue.id):
            return False
        self.issues_dir.mkdir(parents=True, exist_ok=True)
        self._save(issue)
        return True

    def get(self, issue_id: str) -> Issue:
        data = self._read_json(self._issue_path(issue_id))
        if data is None:
            raise CairnError(f"issue not found: {issue_id}")
        return Issue.from_dict(data)

    def exists(self, issue_id: str) -> bool:
        return self._issue_path(issue_id).exists()

    def all(self) -> list[Issue]:
        if not self.issues_dir.exists():
            return []
        return [
            Issue.from_dict(self._read_json(p))
            for p in sorted(self.issues_dir.glob("*.json"))
        ]

    def update(self, issue_id: str, **changes) -> Issue:
        """Apply non-None field changes; keeps ``closed_at`` consistent."""
        issue = self.get(issue_id)
        for key, value in changes.items():
            if value is None:
                continue
            if not hasattr(issue, key):
                raise CairnError(f"unknown field: {key}")
            setattr(issue, key, value)
        if issue.status == Status.CLOSED.value and not issue.closed_at:
            issue.closed_at = now()
        if issue.status != Status.CLOSED.value:
            issue.closed_at = None
        issue.touch()
        self._save(issue)
        return issue

    def claim(self, issue_id: str, who: str) -> Issue:
        """Atomically take a task: set assignee and mark in-progress."""
        return self.update(
            issue_id, assignee=who, status=Status.IN_PROGRESS.value
        )

    def close(self, issue_id: str) -> Issue:
        return self.update(issue_id, status=Status.CLOSED.value)

    def reopen(self, issue_id: str) -> Issue:
        return self.update(issue_id, status=Status.OPEN.value)

    def add_dep(self, issue_id: str, blocked_by: str) -> Issue:
        if blocked_by == issue_id:
            raise CairnError("an issue cannot block itself")
        if not self.exists(blocked_by):
            raise CairnError(f"blocker does not exist: {blocked_by}")
        issue = self.get(issue_id)
        if blocked_by not in issue.blocked_by:
            issue.blocked_by.append(blocked_by)
            issue.touch()
            self._save(issue)
        return issue

    def remove_dep(self, issue_id: str, blocked_by: str) -> Issue:
        issue = self.get(issue_id)
        if blocked_by in issue.blocked_by:
            issue.blocked_by.remove(blocked_by)
            issue.touch()
            self._save(issue)
        return issue

    def set_parent(self, issue_id: str, parent: str | None) -> Issue:
        if parent and not self.exists(parent):
            raise CairnError(f"parent does not exist: {parent}")
        issue = self.get(issue_id)
        issue.parent = parent
        issue.touch()
        self._save(issue)
        return issue

    # ---- the graph -------------------------------------------------------
    def ready(self) -> list[Issue]:
        """Open issues whose every blocker is closed (or gone).

        This is the whole point of the dependency graph: the actionable
        work-front. Sorted by priority then age so the most urgent, oldest
        ready task comes first.
        """
        issues = self.all()
        by_id = {i.id: i for i in issues}
        out = []
        for issue in issues:
            if issue.status != Status.OPEN.value:
                continue
            blocked = any(
                b in by_id and by_id[b].status != Status.CLOSED.value
                for b in issue.blocked_by
            )
            if not blocked:
                out.append(issue)
        out.sort(key=lambda i: (i.priority, i.created))
        return out

    # ---- agent memory ----------------------------------------------------
    def remember(self, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": now(), "text": text}, ensure_ascii=False)
        with self.memory_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def memories(self) -> list[dict]:
        if not self.memory_path.exists():
            return []
        out = []
        for raw in self.memory_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
        return out
