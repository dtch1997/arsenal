"""threads config + spool paths.

The spool lives under ``~/.threads/`` (override the whole base with
``THREADS_HOME`` — tests and smoke runs point it at a scratch dir so they never
touch the real spool). The three *input* locations are overridable too, so
tests can point at fixture trees without monkeypatching ``Path.home``:

- ``THREADS_PROJECTS_DIR`` — the ``~/.claude/projects`` transcript tree.
- ``THREADS_MEMORY_DIR``   — the ``~/jarvis-memory`` thread registry.
- ``THREADS_CONCIERGE_HOME`` — the ``~/concierge-home`` root (for task JSONs).

Summaries are derived-but-durable: transcripts age out of ``~/.claude`` on
their own, the spool does not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

# defaults (also documented in the README)
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MAX_CALLS = 100
DEFAULT_DORMANT_DAYS = 14
MODEL = "claude-haiku-4-5-20251001"

# rough haiku pricing (USD per token) — used only for a spend *estimate* in
# state.json when the runner does not report a real cost.
EST_USD_PER_CALL = 0.004


def _home() -> Path:
    return Path(os.environ.get("THREADS_HOME") or Path.home())


def threads_dir() -> Path:
    return _home() / ".threads"


def summaries_dir() -> Path:
    return threads_dir() / "summaries"


def candidates_dir() -> Path:
    return threads_dir() / "candidates"


def assignments_path() -> Path:
    return threads_dir() / "assignments.jsonl"


def state_path() -> Path:
    return threads_dir() / "state.json"


def projects_dir() -> Path:
    override = os.environ.get("THREADS_PROJECTS_DIR")
    return Path(override) if override else Path.home() / ".claude" / "projects"


def memory_dir() -> Path:
    override = os.environ.get("THREADS_MEMORY_DIR")
    return Path(override) if override else Path.home() / "jarvis-memory"


def concierge_home() -> Path:
    override = os.environ.get("THREADS_CONCIERGE_HOME")
    return Path(override) if override else Path.home() / "concierge-home"


def goals_dir() -> Path:
    """The read-only ``jarvis/goals/`` tree (goal files → hierarchy roots)."""
    override = os.environ.get("THREADS_GOALS_DIR")
    if override:
        return Path(override)
    return Path.home() / "jarvis" / "goals"


def config_path() -> Path:
    return threads_dir() / "config.toml"


def hierarchy_path() -> Path:
    return threads_dir() / "hierarchy.md"


def vault_dir() -> Path:
    return threads_dir() / "vault"


# --------------------------------------------------------------------------- #
# config.toml — the tunable knobs (config-first: refine the formula in the
# file, not the code). Written with defaults on first run, never overwritten.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RelevanceConfig:
    w_sessions: float = 1.0
    w_recency: float = 2.0
    tau: float = 7.0
    window_days: int = 30


@dataclass(frozen=True)
class Config:
    relevance: RelevanceConfig = RelevanceConfig()
    dormant_days: int = DEFAULT_DORMANT_DAYS
    # auto-drafted programs: a cluster of >= this many sibling threads sharing a
    # repo/keyword and no common parent is drafted into hierarchy.md.
    cluster_min_siblings: int = 3


DEFAULT_CONFIG_TOML = """\
# threads config — tunable knobs. Edit and re-run `threads render`/`serve`;
# nothing here is hard-coded in the package. Delete a line to fall back to the
# built-in default.

[relevance]
# relevance = w_sessions*log1p(sessions_in_window)
#           + w_recency*exp(-days_since_last_session / tau)
w_sessions = 1.0
w_recency = 2.0
tau = 7.0
window_days = 30

[dormancy]
# a thread with no activity in this many days (whose stub still reads active)
# is flagged dormant.
days = 14

[clustering]
# >= this many sibling threads sharing a repo/keyword with no common parent get
# an auto-drafted program section in hierarchy.md.
min_siblings = 3
"""


def _coerce_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_config() -> Config:
    """Read ``config.toml`` over the built-in defaults (missing file/keys → defaults)."""
    base = Config()
    path = config_path()
    if tomllib is None or not path.exists():
        return base
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return base
    rel = data.get("relevance") or {}
    relevance = RelevanceConfig(
        w_sessions=_coerce_float(rel.get("w_sessions"), base.relevance.w_sessions),
        w_recency=_coerce_float(rel.get("w_recency"), base.relevance.w_recency),
        tau=_coerce_float(rel.get("tau"), base.relevance.tau),
        window_days=_coerce_int(rel.get("window_days"), base.relevance.window_days),
    )
    dorm = data.get("dormancy") or {}
    clus = data.get("clustering") or {}
    return replace(
        base,
        relevance=relevance,
        dormant_days=_coerce_int(dorm.get("days"), base.dormant_days),
        cluster_min_siblings=_coerce_int(
            clus.get("min_siblings"), base.cluster_min_siblings),
    )


def ensure_spool() -> None:
    for d in (threads_dir(), summaries_dir(), candidates_dir()):
        d.mkdir(parents=True, exist_ok=True)
    path = config_path()
    if not path.exists():
        try:
            path.write_text(DEFAULT_CONFIG_TOML)
        except OSError:
            pass
