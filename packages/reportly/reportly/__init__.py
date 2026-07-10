"""reportly — a standard for experiment reports: scaffold, lint, build.

    import reportly

    # scaffold a new report from the template
    reportly.scaffold("my-experiment", "reports")

    # lint a file or a reports directory against the standard
    cfg = reportly.load_config("reports")
    issues = reportly.lint_path("reports", cfg)

    # render the static report site (md -> html + index)
    reportly.build("reports", config=cfg)

The standard (defaults; override via ``reportly.toml``):
  - front matter: ``vibe: positive|negative|mixed`` (+ optional ``preliminary``)
  - H1 reads as a finding (a sentence), not a topic label
  - required sections: TL;DR · Setup · Result · Reproduce
  - Reproduce carries exact commands; figures referenced must exist
  - close with an italic Branch / Model / Artifacts / Code provenance footer
"""
from .build import build
from .config import Config, load as load_config
from .core import Report, parse
from .lint import Issue, is_failure, lint_file, lint_path, lint_report
from .scaffold import scaffold

__all__ = [
    "build",
    "Config",
    "load_config",
    "Report",
    "parse",
    "Issue",
    "is_failure",
    "lint_file",
    "lint_path",
    "lint_report",
    "scaffold",
]

__version__ = "0.1.0"
