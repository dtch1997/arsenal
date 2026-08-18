"""gazette config: ``~/.config/gazette/config.toml``, created on first run with
the defaults written as comments so a human can see (and edit) what is in
effect."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "github_repos": ["ArcadiaImpact/jarvis", "dtch1997/arsenal"],
    "delay_hours": 36,
    "protected_globs": [
        "CLAUDE.md",
        "HOUSE_RULES.md",
        "ops/**",
        ".claude/**",
        "goals/README.md",
    ],
    "blocked_globs": [
        "**/.env*",
        "**/*secret*",
        "**/*credential*",
    ],
    "notes_window_hours": 24,
    "delay_editions": 2,
    "synthesis_cmd": "claude -p --model sonnet",
}

_TEMPLATE = """\
# gazette config — consumer-mode PR flow (merge sweep + patch notes).
# Uncomment and edit any key to override its default (shown below).

# github_repos = ["ArcadiaImpact/jarvis", "dtch1997/arsenal"]
# delay_hours = 36
# protected_globs = ["CLAUDE.md", "HOUSE_RULES.md", "ops/**", ".claude/**", "goals/README.md"]
# blocked_globs = ["**/.env*", "**/*secret*", "**/*credential*"]
# notes_window_hours = 24
# delay_editions = 2          # delay lane merges after appearing in N morning editions
# synthesis_cmd = "claude -p --model sonnet"   # "" disables the LLM news pass
"""


@dataclass
class Config:
    github_repos: list[str] = field(default_factory=lambda: list(DEFAULTS["github_repos"]))
    delay_hours: int = DEFAULTS["delay_hours"]
    protected_globs: list[str] = field(default_factory=lambda: list(DEFAULTS["protected_globs"]))
    blocked_globs: list[str] = field(default_factory=lambda: list(DEFAULTS["blocked_globs"]))
    notes_window_hours: int = DEFAULTS["notes_window_hours"]
    delay_editions: int = DEFAULTS["delay_editions"]
    synthesis_cmd: str = DEFAULTS["synthesis_cmd"]


def _home() -> Path:
    return Path(os.environ.get("GAZETTE_HOME") or Path.home())


def config_path() -> Path:
    override = os.environ.get("GAZETTE_CONFIG")
    return Path(override) if override else _home() / ".config" / "gazette" / "config.toml"


def spool_dir() -> Path:
    return _home() / ".gazette"


def load_config() -> Config:
    """Load config, creating the commented-defaults template on first run."""
    path = config_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_TEMPLATE)
        except OSError:
            pass
        return Config()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    merged = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    return Config(
        github_repos=list(merged["github_repos"]),
        delay_hours=int(merged["delay_hours"]),
        protected_globs=list(merged["protected_globs"]),
        blocked_globs=list(merged["blocked_globs"]),
        notes_window_hours=int(merged["notes_window_hours"]),
        delay_editions=int(merged["delay_editions"]),
        synthesis_cmd=str(merged["synthesis_cmd"]),
    )
