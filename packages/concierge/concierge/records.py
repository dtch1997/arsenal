"""Task records, mailbox, and the on-disk state layout — the reconciler's ground truth.

Everything is dumb files, atomically written; the daemon holds no state that
isn't reconstructible from this directory plus the OS process table.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

ACTIVE = ("queued", "running", "blocked")
TERMINAL = ("done", "failed", "cancelled")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id() -> str:
    return "t-" + time.strftime("%m%d") + "-" + secrets.token_hex(2)


class Home:
    """CONCIERGE_HOME state directory."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        for d in ("tasks", "specs", "mailbox", "logs", "workspaces"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    @classmethod
    def locate(cls, explicit=None) -> "Home":
        return cls(explicit or os.environ.get("CONCIERGE_HOME", "./concierge-home"))

    # -- tasks --

    def task_path(self, tid) -> Path:
        return self.root / "tasks" / f"{tid}.json"

    def load(self, tid) -> dict:
        return json.loads(self.task_path(tid).read_text())

    def save(self, task) -> None:
        task["updated"] = now_iso()
        p = self.task_path(task["id"])
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(task, indent=2))
        os.replace(tmp, p)

    def tasks(self) -> list[dict]:
        return sorted(
            (json.loads(p.read_text()) for p in (self.root / "tasks").glob("t-*.json")),
            key=lambda t: t["created"],
        )

    # -- mailbox --

    def mailbox_path(self, tid) -> Path:
        return self.root / "mailbox" / f"{tid}.jsonl"

    def post(self, tid, sender, text, via="cli") -> dict:
        entry = {"from": sender, "text": text, "ts": now_iso(), "via": via}
        with self.mailbox_path(tid).open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def messages(self, tid) -> list[dict]:
        p = self.mailbox_path(tid)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    # -- paths --

    def workspace(self, tid) -> Path:
        return self.root / "workspaces" / tid

    def log_dir(self, tid, attempt_n) -> Path:
        return self.root / "logs" / tid / f"attempt-{attempt_n}"

    def spec_path(self, tid) -> Path:
        return self.root / "specs" / f"{tid}.md"


def load_config(home: "Home") -> dict:
    p = home.root / "config.yaml"
    if p.exists():
        try:
            import yaml
            return yaml.safe_load(p.read_text()) or {}
        except ImportError:
            pass
    return {}


def new_task(tid, title, gate, budget, workspace, priority=0, notify=None,
             max_attempts=3, output_schema=None) -> dict:
    return {
        "id": tid,
        "title": title,
        "spec": f"specs/{tid}.md",
        "workspace": workspace,
        "gate": gate,
        "budget": budget,
        "output_schema": output_schema,
        "output": None,
        "result_text": None,
        "priority": priority,
        "status": "queued",
        "status_detail": "",
        "gate_result": None,
        "attempts": [],
        "max_attempts": max_attempts,
        "mail_delivered": 0,
        "notify": notify or ["stdout"],
        "links": {"pr": None, "report": None, "dashboard": None},
        "created": now_iso(),
        "updated": now_iso(),
    }
