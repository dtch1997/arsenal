"""Lint experiment reports against the standard.

Each rule yields zero or more :class:`Issue`s with a severity. ``ERROR`` rules are
hard requirements (missing required section, bad vibe value, broken figure ref);
``WARN`` rules are heuristic conventions (thesis reads as a sentence, a provenance
footer is present, Result leads with a figure). Whether warnings *fail* the lint
is controlled by ``config.level`` (``"error"`` vs ``"warn"``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import core
from .config import Config

ERROR = "error"
WARN = "warn"


@dataclass
class Issue:
    path: Path
    rule: str
    severity: str
    message: str
    line: int | None = None

    def format(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else str(self.path)
        return f"{loc}: {self.severity.upper()} [{self.rule}] {self.message}"


def _has_section(report: core.Report, aliases: list[str]) -> bool:
    return any(any(a in anc.norm for a in aliases) for anc in report.anchors)


_TABLE_ROW = re.compile(r"^\s*\|?[ :|-]*-{1,}[ :|-]*\|", re.M)


def _section_span(report: core.Report, head: core.Anchor) -> str:
    """Body text of a section: from a heading to the next heading of <= its level."""
    lines = report.body.splitlines()
    start = head.line  # 1-based; content begins on the next line
    end = len(lines)
    for a in report.anchors:
        if a.level >= 1 and a.level <= head.level and a.line > head.line:
            end = a.line - 1
            break
    return "\n".join(lines[start:end])


# --- individual rules: each takes (report, config) -> Iterable[Issue] ----------

def _rule_thesis_h1(r: core.Report, c: Config):
    h1s = r.headings(level=1)
    if not h1s:
        yield Issue(r.path, "thesis_h1", ERROR, "no H1 title found")
        return
    if len(h1s) > 1:
        yield Issue(r.path, "thesis_h1", WARN,
                    f"{len(h1s)} H1 headings; a report should have exactly one",
                    line=h1s[1].line)
    words = len(re.findall(r"\w+", h1s[0].text))
    if words < c.min_thesis_words:
        yield Issue(r.path, "thesis_h1", WARN,
                    f"title looks like a topic, not a finding "
                    f"({words} words < {c.min_thesis_words}); state the result as a sentence",
                    line=h1s[0].line)


def _rule_frontmatter(r: core.Report, c: Config):
    vibe = r.meta.get("vibe", "").strip().lower()
    if vibe and vibe not in [v.lower() for v in c.vibe_values]:
        yield Issue(r.path, "frontmatter", ERROR,
                    f"vibe={vibe!r} not in {c.vibe_values}")
    prelim = r.meta.get("preliminary")
    if prelim is not None and prelim.strip().lower() not in (
            "true", "false", "yes", "no", "1", "0", ""):
        yield Issue(r.path, "frontmatter", WARN,
                    f"preliminary={prelim!r} is not boolean-ish")


def _rule_required_sections(r: core.Report, c: Config):
    for kind in c.required:
        if not _has_section(r, c.aliases(kind)):
            yield Issue(r.path, "required_sections", ERROR,
                        f"missing required section: {kind} "
                        f"(any of: {', '.join(c.aliases(kind))})")


def _rule_reproduce_commands(r: core.Report, c: Config):
    # Only meaningful when a Reproduce section actually exists; a missing section
    # is already covered by the required_sections rule.
    if not _has_section(r, c.aliases("reproduce")):
        return
    if not any(lang in ("bash", "sh", "shell", "console", "") and code.strip()
               for lang, code, _ in r.code_blocks):
        yield Issue(r.path, "reproduce_commands", WARN,
                    "Reproduce section should contain a fenced command block "
                    "(exact commands to regenerate the result)")


def _rule_figures_exist(r: core.Report, c: Config):
    for fig in r.figures:
        if not fig.is_local:
            continue
        target = (r.path.parent / fig.src).resolve()
        if not target.exists():
            yield Issue(r.path, "figures_exist", ERROR,
                        f"figure not found: {fig.src}", line=fig.line)


def _rule_result_figure(r: core.Report, c: Config):
    """Convention: each Result section presents its evidence — a figure or a table."""
    result_aliases = c.aliases("result")
    result_heads = [a for a in r.anchors if a.level >= 1
                    and any(al in a.norm for al in result_aliases)]
    for head in result_heads:
        span = _section_span(r, head)
        has_fig = "![" in span
        has_table = bool(_TABLE_ROW.search(span))
        if not (has_fig or has_table):
            yield Issue(r.path, "result_figure", WARN,
                        f"Result section {head.text!r} has no figure or table; "
                        "lead the result with its evidence",
                        line=head.line)


def _rule_provenance_footer(r: core.Report, c: Config):
    """Convention: an italic Branch/Model/Artifacts/Code line (it may sit before an
    appendix, so scan the whole report rather than only the last paragraph)."""
    for para in (p.strip() for p in r.body.split("\n\n") if p.strip()):
        italic = (para.startswith("*") and para.rstrip().endswith("*")
                  and not para.startswith("**"))
        mentions = sum(k in para.lower() for k in ("branch", "artifact", "code", "model"))
        if italic and mentions >= 2:
            return
    yield Issue(r.path, "provenance_footer", WARN,
                "missing provenance footer (italic line naming "
                "Branch / Model / Artifacts / Code)")


RULES = [
    _rule_thesis_h1,
    _rule_frontmatter,
    _rule_required_sections,
    _rule_reproduce_commands,
    _rule_figures_exist,
    _rule_result_figure,
    _rule_provenance_footer,
]


def lint_report(report: core.Report, config: Config | None = None) -> list[Issue]:
    config = config or Config()
    issues: list[Issue] = []
    for rule in RULES:
        name = rule.__name__.removeprefix("_rule_")
        if name in config.disable:
            continue
        issues.extend(rule(report, config))
    issues.sort(key=lambda i: (i.line or 0))
    return issues


def lint_file(path: str | Path, config: Config | None = None) -> list[Issue]:
    return lint_report(core.parse(path), config)


def lint_path(path: str | Path, config: Config | None = None) -> dict[Path, list[Issue]]:
    """Lint a file or every ``*.md`` (except README) under a directory."""
    p = Path(path)
    files = [p] if p.is_file() else sorted(
        f for f in p.glob("*.md") if f.name.lower() != "readme.md")
    return {f: lint_file(f, config) for f in files}


def is_failure(issues: list[Issue], config: Config) -> bool:
    return any(i.severity == ERROR or (config.fail_on_warn and i.severity == WARN)
               for i in issues)
