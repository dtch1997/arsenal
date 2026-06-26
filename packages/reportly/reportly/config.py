"""Configuration for the report standard.

Defaults encode the model-thrashing convention; a ``reportly.toml`` at (or above)
the reports directory overrides any field. Everything is tunable so a repo can
require more or fewer sections, rename the vibe vocabulary, or flip the strictness
threshold.

Example ``reportly.toml``::

    reports_dir = "reports"
    required = ["tldr", "setup", "result", "reproduce"]
    vibe_values = ["positive", "negative", "mixed"]
    level = "error"          # or "warn" — whether warnings also fail the lint
    disable = ["result_figure"]
    site_title = "myproject — reports"
    site_subtitle = "Self-contained write-ups."

    [sections]               # extend/override the alias map (kind = [aliases])
    setup = ["setup", "method", "protocol"]
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# kind -> substrings that, found in an anchor's text, satisfy that section kind.
#
# A combined heading like "Discussion & next steps" still satisfies both
# `discussion` and `next_steps` (both substrings match), so it stays valid.
DEFAULT_SECTIONS: dict[str, list[str]] = {
    "tldr": ["tl;dr", "tldr", "executive summary", "summary"],
    "context": ["context", "why this matters", "background", "motivation"],
    "setup": ["setup", "method", "protocol"],
    "result": ["result", "finding"],
    "discussion": ["discussion", "takeaway", "implication", "conclusion", "update"],
    "next_steps": ["next steps", "next step", "future work", "follow-up", "what's next"],
    "reproduce": ["reproduce", "reproduction", "reproducibility"],
    "appendix": ["appendix"],
}

DEFAULT_REQUIRED = ["tldr", "setup", "result", "discussion", "next_steps", "reproduce"]
DEFAULT_VIBES = ["positive", "negative", "mixed"]


@dataclass
class Config:
    reports_dir: str = "reports"
    required: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED))
    vibe_values: list[str] = field(default_factory=lambda: list(DEFAULT_VIBES))
    sections: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_SECTIONS.items()})
    level: str = "error"  # "error": only errors fail; "warn": warnings fail too
    disable: list[str] = field(default_factory=list)  # rule names to skip
    min_thesis_words: int = 5
    site_title: str | None = None
    site_subtitle: str | None = None

    @property
    def fail_on_warn(self) -> bool:
        return self.level.strip().lower() == "warn"

    def aliases(self, kind: str) -> list[str]:
        return self.sections.get(kind, [kind])


def _find_config(start: Path) -> Path | None:
    """Walk up from ``start`` (a dir or file) looking for ``reportly.toml``."""
    start = start if start.is_dir() else start.parent
    for d in (start, *start.parents):
        cand = d / "reportly.toml"
        if cand.is_file():
            return cand
    return None


def load(start: str | Path = ".") -> Config:
    cfg = _find_config(Path(start).resolve())
    if cfg is None:
        return Config()
    data = tomllib.loads(cfg.read_text())
    base = Config()
    sections = dict(base.sections)
    sections.update(data.get("sections", {}))
    return Config(
        reports_dir=data.get("reports_dir", base.reports_dir),
        required=data.get("required", base.required),
        vibe_values=data.get("vibe_values", base.vibe_values),
        sections=sections,
        level=data.get("level", base.level),
        disable=data.get("disable", base.disable),
        min_thesis_words=data.get("min_thesis_words", base.min_thesis_words),
        site_title=data.get("site_title", base.site_title),
        site_subtitle=data.get("site_subtitle", base.site_subtitle),
    )
