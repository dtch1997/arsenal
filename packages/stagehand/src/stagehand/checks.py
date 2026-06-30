"""checks — reusable correctness predicates for gating steps.

A step's *body* does the work; a `check` says whether it actually succeeded. These
are the common criteria, as small composable predicates. Each evaluates a value and
returns a `CheckResult` — a `(ok, issues)` 2-tuple that also supports `&` / `|` /
`~` and truthiness — so it drops straight into the gate shape used by
`Flow.filter`, `with_retry(check=…)`, and `run(check=…)`:

    from stagehand.checks import produced, finite
    good = flow.filter("gate", trained,
                       lambda r: produced(r["ckpt"]) & finite(r["loss"]))

The kernel is stdlib-light. `tests_pass` / `uri_exists` shell out (pytest /
gcloud), like the rest of stagehand's external-tool seams — handy for a gate at a
barrier, but they block, so don't put them on a hot per-item path.
"""
from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
from typing import NamedTuple


class CheckResult(NamedTuple):
    """`(ok, issues)` — a gate result that also composes with `&` / `|` / `~` and
    is truthy on `ok`. Being a tuple, it *is* the gate shape, so it works directly
    as a `filter` / `with_retry` predicate's return value."""
    ok: bool
    issues: list

    def __and__(self, other):            # both must pass; collect every failure's issues
        o = _coerce(other)
        return CheckResult(self.ok and o.ok, list(self.issues) + list(o.issues))

    def __or__(self, other):             # either may pass
        o = _coerce(other)
        passed = self.ok or o.ok
        return CheckResult(passed, [] if passed else list(self.issues) + list(o.issues))

    def __invert__(self):
        return CheckResult(not self.ok, [] if not self.ok else ["unexpectedly passed"])

    def __bool__(self):
        return bool(self.ok)


def _coerce(x) -> CheckResult:
    if isinstance(x, CheckResult):
        return x
    if isinstance(x, tuple) and len(x) == 2:
        return CheckResult(bool(x[0]), list(x[1]))
    return CheckResult(bool(x), [] if x else ["check failed"])


def ok() -> CheckResult:
    return CheckResult(True, [])


def fail(msg) -> CheckResult:
    return CheckResult(False, [str(msg)])


def require(cond, msg) -> CheckResult:
    """Lift a plain condition into a check with a failure message."""
    return CheckResult(bool(cond), [] if cond else [str(msg)])


# ---- files / artifacts ---------------------------------------------------- #
def exists(path) -> CheckResult:
    return require(Path(path).exists(), f"missing: {path}")


def produced(path) -> CheckResult:
    """The file exists and is non-empty (an artifact actually got written)."""
    p = Path(path)
    if not p.exists():
        return fail(f"missing: {path}")
    try:
        size = p.stat().st_size
    except OSError:
        return fail(f"unreadable: {path}")
    return require(size > 0, f"empty: {path}")


def json_has(path, keys) -> CheckResult:
    """`path` is valid JSON containing all of `keys`."""
    p = Path(path)
    if not p.exists():
        return fail(f"missing: {path}")
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return fail(f"bad json {path}: {e}")
    missing = [k for k in keys if k not in data]
    return require(not missing, f"missing keys {missing} in {path}")


_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"%PDF")


def valid_image(path) -> CheckResult:
    """`path` is a non-empty file with a recognized image header (PNG/JPEG/GIF/PDF,
    or an SVG/XML opening tag) — stdlib magic-byte sniff, no Pillow needed."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return fail(f"missing/empty: {path}")
    head = p.read_bytes()[:8]
    if any(head.startswith(m) for m in _IMAGE_MAGIC) or head.lstrip().startswith(b"<"):
        return ok()
    return fail(f"not a recognized image: {path}")


# ---- numbers / metrics ---------------------------------------------------- #
def finite(x) -> CheckResult:
    """`x` is a real, finite number (catches NaN / inf / None — a diverged run)."""
    try:
        return require(x is not None and math.isfinite(float(x)), f"not finite: {x!r}")
    except (TypeError, ValueError):
        return fail(f"not a number: {x!r}")


def in_range(x, lo=None, hi=None) -> CheckResult:
    f = finite(x)
    if not f:
        return f
    v = float(x)
    if lo is not None and v < lo:
        return fail(f"{v} < {lo}")
    if hi is not None and v > hi:
        return fail(f"{v} > {hi}")
    return ok()


# ---- processes / agents --------------------------------------------------- #
def exit_ok(result) -> CheckResult:
    """Succeeded: an exit code of 0, a `.returncode == 0`, or a truthy `.ok`
    (e.g. an `AgentOutcome`)."""
    if hasattr(result, "ok"):
        return require(bool(result.ok), "not ok")
    if hasattr(result, "returncode"):
        return require(result.returncode == 0, f"exit {result.returncode}")
    return require(result == 0, f"exit {result}")


# ---- external-tool seams (shell out; block) ------------------------------- #
def tests_pass(cwd=".", *, args=("-q",), pytest=("python", "-m", "pytest")) -> CheckResult:
    """Run pytest in `cwd`; ok on exit 0, else the tail of the output as the issue.
    Blocks — meant for a gate, not a hot path. The failure text makes good
    `with_retry` feedback for a coding agent."""
    proc = subprocess.run([*pytest, *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode == 0:
        return ok()
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-15:]
    return CheckResult(False, ["tests failed:\n" + "\n".join(tail)])


def uri_exists(uri, *, tool=None) -> CheckResult:
    """A remote object exists — `gs://…` via gcloud, `s3://…` via aws. Best-effort;
    blocks. Use to make persist steps idempotent (skip if already uploaded)."""
    cmd = _uri_ls_cmd(uri, tool)
    if cmd is None:
        return fail(f"don't know how to check {uri}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return require(proc.returncode == 0, f"not found: {uri}")


def _uri_ls_cmd(uri, tool):
    if tool:
        return [*tool, uri]
    if uri.startswith("gs://"):
        return ["gcloud", "storage", "ls", uri]
    if uri.startswith("s3://"):
        return ["aws", "s3", "ls", uri]
    return None
