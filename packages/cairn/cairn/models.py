"""Core data model: the Issue record, its status, and hash-based id minting."""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Status(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


# Free-form, but these are the ones the CLI advertises and `prime` explains.
KNOWN_TYPES = ("task", "bug", "epic", "chore", "note")

MIN_PRIORITY = 0  # P0 = most urgent
MAX_PRIORITY = 3  # P3 = least urgent


def now() -> str:
    """UTC timestamp, second resolution — stable and diff-friendly in JSON."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix: str, title: str) -> str:
    """Mint a short, collision-resistant id like ``cn-a1b2``.

    The id is a hash of the title, current time, and random bytes, so two
    agents creating issues at the same moment on different branches never
    collide — no central counter to contend on. Four hex chars (65k space);
    the store retries on the rare within-repo clash.
    """
    seed = f"{title}|{now()}|{secrets.token_hex(8)}".encode("utf-8")
    digest = hashlib.blake2b(seed, digest_size=4).hexdigest()[:4]
    return f"{prefix}-{digest}"


@dataclass
class Issue:
    id: str
    title: str
    description: str = ""
    status: str = Status.OPEN.value
    priority: int = 2
    type: str = "task"
    assignee: str | None = None
    labels: list = field(default_factory=list)
    blocked_by: list = field(default_factory=list)
    parent: str | None = None
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    closed_at: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def touch(self) -> None:
        self.updated = now()
