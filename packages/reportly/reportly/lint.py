"""Lint experiment reports against the standard.

Each rule yields zero or more :class:`Issue`s with a severity. ``ERROR`` rules are
hard requirements (missing required section, an unanswered question, bad vibe
value, broken figure ref); ``WARN`` rules are heuristic conventions (thesis reads
as a sentence, answers cite evidence, the answer sheet leads, a provenance footer
is present, Evidence leads with a figure). Whether warnings *fail* the lint is
controlled by ``config.level`` (``"error"`` vs ``"warn"``).

Rules are layer-aware (the two-audience convention, REPORTING.md): *rendered*
checks (thesis H1, the answer sheet, evidence figures, section ordering, most
required sections) must hold with ``<!-- internal: -->`` comment blocks
stripped — content hiding in a comment can't satisfy them; *whole-file* checks
(Reproduce commands, provenance footer, figure files existing, and the section
kinds in ``config.internal_ok``) accept either layer, since plumbing
conventionally lives in comments.

These are the *mechanical* guarantees of the standard; the content rubric they
serialize lives in ``REPORTING.md``.
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


def _has_section(report: core.Report, aliases: list[str],
                 anchors: list[core.Anchor] | None = None) -> bool:
    anchors = report.anchors if anchors is None else anchors
    return any(any(a in anc.norm for a in aliases) for anc in anchors)


_TABLE_ROW = re.compile(r"^\s*\|?[ :|-]*-{1,}[ :|-]*\|", re.M)
# An answer-sheet item: a paragraph opening with ``**Qn. <question>**``; the rest
# of the same paragraph is the answer.
_QUESTION_LEAD = re.compile(r"\s*\*\*(?P<q>q\d+\b[^*]*?)\*\*[ \t]*\n?(?P<a>.*)", re.I | re.S)
# What counts as "the answer points at its evidence" (heuristic): a Fig/Table
# mention, a markdown link, a section ref, or an explicit "Not answered".
_EVIDENCE_HINT = re.compile(r"\bfig|\btab(le)?\b|\]\(|§|not answered", re.I)


def _rendered_span(report: core.Report, head: core.Anchor) -> str:
    """Rendered body text of a section: from a heading to the next *rendered*
    heading of <= its level, with internal-layer (commented) lines blanked so
    content hiding in a comment can't satisfy a rendered-projection check."""
    lines = report.body.splitlines()
    start = head.line  # 1-based; content begins on the next line
    end = len(lines)
    for a in report.anchors:
        if (a.level >= 1 and a.level <= head.level and a.line > head.line
                and not report.in_comment(a.line)):
            end = a.line - 1
            break
    return "\n".join("" if report.in_comment(start + 1 + i) else ln
                     for i, ln in enumerate(lines[start:end]))


def _paragraphs(span: str, first_line: int):
    """Yield ``(absolute_start_line, text)`` per blank-line-separated paragraph."""
    cur: list[str] = []
    start = None
    for i, ln in enumerate(span.splitlines()):
        if ln.strip():
            if start is None:
                start = first_line + i
            cur.append(ln)
        elif cur:
            yield start, "\n".join(cur)
            cur, start = [], None
    if cur:
        yield start, "\n".join(cur)


def _first_heading(anchors: list[core.Anchor], aliases: list[str]) -> core.Anchor | None:
    return next((a for a in anchors
                 if a.level >= 1 and any(al in a.norm for al in aliases)), None)


# --- individual rules: each takes (report, config) -> Iterable[Issue] ----------

def _rule_thesis_h1(r: core.Report, c: Config):
    h1s = [a for a in r.rendered_anchors() if a.level == 1]
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
    rendered = r.rendered_anchors()
    for kind in c.required:
        # Plumbing kinds (internal_ok) may satisfy from a comment block;
        # everything else must exist in the rendered projection.
        anchors = r.anchors if kind in c.internal_ok else rendered
        if not _has_section(r, c.aliases(kind), anchors):
            yield Issue(r.path, "required_sections", ERROR,
                        f"missing required section: {kind} "
                        f"(any of: {', '.join(c.aliases(kind))})")


def _rule_questions_answered(r: core.Report, c: Config):
    """The answer sheet: >= 1 ``**Qn. …?**`` item, each answered in the same
    paragraph (\"Not answered — <why>\" counts), each answer citing evidence."""
    aliases = c.aliases("questions")
    heads = [a for a in r.rendered_anchors() if any(al in a.norm for al in aliases)]
    if not heads:
        return  # a missing section is already covered by required_sections
    head = next((h for h in heads if h.level >= 1), heads[0])
    span = _rendered_span(r, head)

    found = 0
    for start, para in _paragraphs(span, head.line + 1):
        m = _QUESTION_LEAD.match(para)
        if not m:
            continue
        found += 1
        q, a = m.group("q").strip(), m.group("a").strip()
        if not a:
            yield Issue(r.path, "questions_answered", ERROR,
                        f"unanswered question: {q[:60]!r} — answer it in place "
                        "(\"Not answered — <why>\" is a valid answer)", line=start)
        elif not _EVIDENCE_HINT.search(a):
            yield Issue(r.path, "questions_answered", WARN,
                        f"answer to {q[:40]!r} cites no evidence (Fig/Table/link)",
                        line=start)
    if not found:
        yield Issue(r.path, "questions_answered", ERROR,
                    "Questions section has no `**Qn. …?**` items "
                    "(bold question, answer directly beneath in the same paragraph)",
                    line=head.line)


def _rule_answers_first(r: core.Report, c: Config):
    """Convention: the answer sheet leads; What-was-run/Setup comes after."""
    rendered = r.rendered_anchors()
    q = _first_heading(rendered, c.aliases("questions"))
    s = _first_heading(rendered, c.aliases("setup"))
    if q and s and s.line < q.line:
        yield Issue(r.path, "answers_first", WARN,
                    "paper-order: Setup/What-was-run appears before the Questions "
                    "answer sheet; lead with the answers (see REPORTING.md)",
                    line=s.line)


def _rule_reproduce_commands(r: core.Report, c: Config):
    # Whole-file check: the Reproduce section and its commands are plumbing, so
    # both may live inside an <!-- internal: --> block. Only meaningful when a
    # Reproduce section actually exists (either layer); a missing section is
    # already covered by the required_sections rule.
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
    """Convention: each Evidence/Result section presents its evidence — a figure
    or a table."""
    result_aliases = c.aliases("result")
    result_heads = [a for a in r.rendered_anchors() if a.level >= 1
                    and any(al in a.norm for al in result_aliases)]
    for head in result_heads:
        span = _rendered_span(r, head)
        has_fig = "![" in span
        has_table = bool(_TABLE_ROW.search(span))
        if not (has_fig or has_table):
            yield Issue(r.path, "result_figure", WARN,
                        f"Evidence section {head.text!r} has no figure or table; "
                        "lead the result with its evidence",
                        line=head.line)


def _rule_provenance_footer(r: core.Report, c: Config):
    """Convention: an italic Branch/Model/Artifacts/Code line (it may sit before an
    appendix, so scan the whole report rather than only the last paragraph).
    Whole-file check: the footer is plumbing, so it may live inside an
    <!-- internal: --> block — scan comment inners too."""
    candidates = [p for p in r.body.split("\n\n")]
    # comment inners, per paragraph and per line (a footer often sits directly
    # under the block's ``internal: …`` label line, with no blank line between)
    for _, inner in r.comments:
        candidates += inner.split("\n\n") + inner.splitlines()
    for para in (p.strip() for p in candidates if p.strip()):
        italic = (para.startswith("*") and para.rstrip().endswith("*")
                  and not para.startswith("**"))
        mentions = sum(k in para.lower() for k in ("branch", "artifact", "code", "model"))
        if italic and mentions >= 2:
            return
    yield Issue(r.path, "provenance_footer", WARN,
                "missing provenance footer (italic line naming "
                "Branch / Model / Artifacts / Code)")


def _rule_plumbing_outside(r: core.Report, c: Config):
    """Two-audience convention: once a report carries <!-- internal: --> blocks,
    Reproduce-style multi-command fences belong inside them, not in rendered
    prose. Scoped narrowly — the recipe voice legitimately quotes the one
    command or input that defines the method, so single-command fences (and
    reports that don't use internal blocks at all) never warn."""
    if not r.has_internal_blocks:
        return
    for lang, code, line in r.code_blocks:
        if lang not in ("bash", "sh", "shell", "console") or r.in_comment(line):
            continue
        commands = [ln for ln in code.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
        if len(commands) >= 2:
            yield Issue(r.path, "plumbing_outside", WARN,
                        f"{len(commands)}-command fence in rendered prose; this "
                        "report uses internal blocks — move plumbing into an "
                        "<!-- internal: --> block (quoting one defining command "
                        "in the rendered text is fine)", line=line)


RULES = [
    _rule_thesis_h1,
    _rule_frontmatter,
    _rule_required_sections,
    _rule_questions_answered,
    _rule_answers_first,
    _rule_reproduce_commands,
    _rule_figures_exist,
    _rule_result_figure,
    _rule_provenance_footer,
    _rule_plumbing_outside,
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
