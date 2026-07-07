"""Gate objects — externally-evaluated completion predicates.

A Gate is declarative data with behavior, not a closure: it must round-trip
through the task record JSON, because the process that evaluates it (the
reconciler, possibly restarted or on another host) is not the process that
submitted it. Dataclass fields are the serialization surface; subclasses
auto-register by `kind`. Custom gates: subclass Gate in a module the daemon
imports.

The contract:
  check(ctx)  -> Verdict(passed, detail, links)  # evidence, not just a bool
  describe()  -> str        # shown to the worker in its preamble
  to_json() / Gate.from_json(data)
  g1 & g2 / g1 | g2         # AllOf / AnyOf composition
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class Verdict:
    passed: bool
    detail: str
    links: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed


@dataclass(frozen=True)
class GateContext:
    workspace: Path
    task: dict


_REGISTRY: dict[str, type["Gate"]] = {}


class Gate:
    kind: ClassVar[str]

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if hasattr(cls, "kind"):
            _REGISTRY[cls.kind] = cls

    # -- contract --

    def check(self, ctx: GateContext) -> Verdict:
        raise NotImplementedError

    def describe(self) -> str:
        return repr(self)

    # -- serialization --

    def to_json(self) -> dict:
        return {"kind": self.kind, **{f.name: getattr(self, f.name) for f in fields(self)}}

    @staticmethod
    def from_json(data: dict) -> "Gate":
        data = dict(data)
        kind = data.pop("kind")
        try:
            cls = _REGISTRY[kind]
        except KeyError:
            raise ValueError(
                f"unknown gate kind {kind!r} — is the module defining it imported?") from None
        return cls._from_fields(data)

    @classmethod
    def _from_fields(cls, data: dict) -> "Gate":
        return cls(**data)

    # -- composition --

    def __and__(self, other: "Gate") -> "AllOf":
        return AllOf((self, other))

    def __or__(self, other: "Gate") -> "AnyOf":
        return AnyOf((self, other))


# -- built-ins --


@dataclass(frozen=True)
class Always(Gate):
    kind: ClassVar[str] = "always"

    def check(self, ctx):
        return Verdict(True, "always")

    def describe(self):
        return "always passes (no gate)"


@dataclass(frozen=True)
class FileExists(Gate):
    path: str
    kind: ClassVar[str] = "file_exists"

    def check(self, ctx):
        present = (ctx.workspace / self.path).exists()
        return Verdict(present, f"file_exists({self.path}): {'present' if present else 'missing'}")

    def describe(self):
        return f"the file `{self.path}` exists in the workspace"


@dataclass(frozen=True)
class ShellOk(Gate):
    cmd: str
    timeout: float = 600.0
    kind: ClassVar[str] = "shell_ok"

    def check(self, ctx):
        try:
            r = subprocess.run(self.cmd, shell=True, cwd=ctx.workspace,
                               capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return Verdict(False, f"shell_ok: timed out after {self.timeout:.0f}s: {self.cmd}")
        tail = (r.stdout + r.stderr).strip()[-500:]
        return Verdict(r.returncode == 0, f"shell_ok rc={r.returncode}: {tail or self.cmd}")

    def describe(self):
        return f"`{self.cmd}` exits 0 when run in the workspace"


@dataclass(frozen=True)
class PrOpen(Gate):
    branch: str | None = None  # None → the task's own branch
    kind: ClassVar[str] = "pr_open"
    want: ClassVar[str] = "OPEN"

    def check(self, ctx):
        branch = self.branch or ctx.task["workspace"]["branch"]
        r = subprocess.run(["gh", "pr", "view", branch, "--json", "state,url"],
                           cwd=ctx.workspace, capture_output=True, text=True)
        if r.returncode != 0:
            return Verdict(False, f"{self.kind}({branch}): no PR ({r.stderr.strip()[:200]})")
        info = json.loads(r.stdout)
        return Verdict(info["state"] == self.want,
                       f"{self.kind}({branch}): state={info['state']} {info['url']}",
                       {"pr": info["url"]})

    def describe(self):
        state = "an open" if self.want == "OPEN" else "a merged"
        return f"{state} PR exists for branch `{self.branch or '<task branch>'}`"


class PrMerged(PrOpen):
    kind: ClassVar[str] = "pr_merged"
    want: ClassVar[str] = "MERGED"


# -- combinators --


@dataclass(frozen=True)
class AllOf(Gate):
    gates: tuple
    kind: ClassVar[str] = "all_of"

    def to_json(self):
        return {"kind": self.kind, "gates": [g.to_json() for g in self.gates]}

    @classmethod
    def _from_fields(cls, data):
        return cls(tuple(Gate.from_json(g) for g in data["gates"]))

    def check(self, ctx):
        details, links = [], {}
        for g in self.gates:
            v = g.check(ctx)
            details.append(v.detail)
            links.update(v.links)
            if not v:
                return Verdict(False, "; ".join(details), links)
        return Verdict(True, "; ".join(details), links)

    def describe(self):
        return " AND ".join(g.describe() for g in self.gates)

    def __and__(self, other):
        return AllOf(self.gates + (other,))


@dataclass(frozen=True)
class AnyOf(Gate):
    gates: tuple
    kind: ClassVar[str] = "any_of"

    def to_json(self):
        return {"kind": self.kind, "gates": [g.to_json() for g in self.gates]}

    @classmethod
    def _from_fields(cls, data):
        return cls(tuple(Gate.from_json(g) for g in data["gates"]))

    def check(self, ctx):
        details, links = [], {}
        for g in self.gates:
            v = g.check(ctx)
            details.append(v.detail)
            links.update(v.links)
            if v:
                return Verdict(True, "; ".join(details), links)
        return Verdict(False, "; ".join(details), links)

    def describe(self):
        return " OR ".join(g.describe() for g in self.gates)

    def __or__(self, other):
        return AnyOf(self.gates + (other,))


def check(task: dict, workspace) -> Verdict:
    """Evaluate a task's serialized gate against its workspace."""
    return Gate.from_json(task["gate"]).check(GateContext(Path(workspace), task))
