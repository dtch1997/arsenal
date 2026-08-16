"""desk config: ``~/.config/desk/config.toml``, created on first run with the
defaults written as comments so a human can see (and edit) what is in effect."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "github_repos": ["ArcadiaImpact/jarvis", "dtch1997/arsenal"],
    "marker_paths": ["~/jarvis-memory", "~/jarvis/goals"],
    "concierge_home": "~/concierge-home",
    "pr_age_warn_days": 7,
    "flare_stale_days": 7,
}

_TEMPLATE = """\
# desk config — everything currently blocked on Daniel, in one place.
# Uncomment and edit any key to override its default (shown below).

# github_repos = ["ArcadiaImpact/jarvis", "dtch1997/arsenal"]
# marker_paths = ["~/jarvis-memory", "~/jarvis/goals"]
# concierge_home = "~/concierge-home"
# pr_age_warn_days = 7
# flare_stale_days = 7
"""


@dataclass
class Config:
    github_repos: list[str] = field(default_factory=lambda: list(DEFAULTS["github_repos"]))
    marker_paths: list[str] = field(default_factory=lambda: list(DEFAULTS["marker_paths"]))
    concierge_home: str = DEFAULTS["concierge_home"]
    pr_age_warn_days: int = DEFAULTS["pr_age_warn_days"]
    flare_stale_days: int = DEFAULTS["flare_stale_days"]

    @property
    def concierge_home_path(self) -> Path:
        return Path(os.path.expanduser(self.concierge_home))

    @property
    def marker_path_list(self) -> list[Path]:
        return [Path(os.path.expanduser(p)) for p in self.marker_paths]


def _home() -> Path:
    return Path(os.environ.get("DESK_HOME") or Path.home())


def config_path() -> Path:
    override = os.environ.get("DESK_CONFIG")
    return Path(override) if override else _home() / ".config" / "desk" / "config.toml"


def load_config() -> Config:
    """Load config, creating the commented-defaults template on first run."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_TEMPLATE)
        return Config()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    merged = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    return Config(
        github_repos=list(merged["github_repos"]),
        marker_paths=list(merged["marker_paths"]),
        concierge_home=merged["concierge_home"],
        pr_age_warn_days=int(merged["pr_age_warn_days"]),
        flare_stale_days=int(merged["flare_stale_days"]),
    )
