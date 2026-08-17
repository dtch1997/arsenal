"""Recursive thread hierarchy — threads of threads.

The v0.1 view is flat (session → thread). Daniel's model is recursive: a
session is the smallest thread; higher-order threads weave lower ones
(session → project thread → program → goal). This module builds that layer:

* **Source of truth** is ``~/.threads/hierarchy.md`` — one ``## <parent>``
  section per parent, children as ``[[slug]]`` wikilink bullets, marked
  agent-drafted/standing. It is loaded, validated (cycles and multi-parent
  rejected — a tree, not a DAG), and it *overrides* everything below.
* **Goal roots for free**: ``jarvis/goals/*.md`` (read-only) are scanned for
  mentions of registry slugs; a mentioned thread gets that goal as parent
  unless hierarchy.md already places it.
* **Auto-drafted programs**: :func:`draft_programs` (called from ``weave``)
  groups unparented sibling threads that share a repo/keyword into a drafted
  ``## program-…`` section.
* **Roll-ups**: :func:`build_forest` aggregates each node's subtree (session
  counts, last-activity max, relevance recomputed from aggregated stats).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import config, registry
from .relevance import ThreadStats, relevance

UNPARENTED = "(unparented)"
AGENT_MARKER = "agent-drafted, standing until Daniel edits"


class HierarchyError(ValueError):
    """hierarchy.md is not a tree (a cycle, or a thread with two parents)."""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --------------------------------------------------------------------------- #
# hierarchy.md parsing
# --------------------------------------------------------------------------- #
_SECTION = re.compile(r"^##\s+(?:(goal|program)\s*[:\-]\s*)?(.+?)\s*$")
_WIKILINK = re.compile(r"\[\[([^\]]+?)\]\]")


@dataclass
class RawHierarchy:
    """The literal edges from hierarchy.md, before goal derivation."""

    edges: list[tuple[str, str]]                 # (parent, child), in file order
    notes: dict[str, str]                        # parent -> section note
    declared_kind: dict[str, str]                # parent -> 'goal'|'program' if tagged
    section_order: list[str]                     # parents in file order


def parse_hierarchy_md(text: str) -> RawHierarchy:
    edges: list[tuple[str, str]] = []
    notes: dict[str, str] = {}
    declared: dict[str, str] = {}
    order: list[str] = []
    current: str | None = None
    note_lines: list[str] = []

    def _flush_note():
        if current is not None and note_lines:
            note = " ".join(x.strip() for x in note_lines).strip()
            if note and AGENT_MARKER not in note:
                notes[current] = note

    for raw in text.splitlines():
        line = raw.rstrip()
        m = _SECTION.match(line)
        if m:
            _flush_note()
            note_lines = []
            kind, name = m.group(1), _norm(m.group(2))
            current = name
            if name not in order:
                order.append(name)
            if kind:
                declared[name] = kind
            continue
        if current is None:
            continue
        bullet = line.lstrip()
        if bullet.startswith(("-", "*")):
            for child in _WIKILINK.findall(bullet):
                edges.append((current, _norm(child)))
        elif bullet and not bullet.startswith("#") and not bullet.startswith("_"):
            note_lines.append(bullet)
    _flush_note()
    return RawHierarchy(edges=edges, notes=notes, declared_kind=declared,
                        section_order=order)


# --------------------------------------------------------------------------- #
# goal files (read-only roots)
# --------------------------------------------------------------------------- #
_FRONT_SLUG = re.compile(r"^slug:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class Goal:
    slug: str
    title: str
    mentions: set[str]                            # registry slugs referenced


def load_goals(reg_slugs: set[str]) -> list[Goal]:
    d = config.goals_dir()
    if not d.is_dir():
        return []
    goals: list[Goal] = []
    for p in sorted(d.glob("*.md")):
        if p.name in ("README.md", "TEMPLATE.md"):
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        m = _FRONT_SLUG.search(text)
        gslug = _norm(m.group(1)) if m else _norm(p.stem)
        title = _first_heading(text) or gslug
        low = text.lower()
        mentions = {
            s for s in reg_slugs
            if re.search(rf"(?<![a-z0-9-]){re.escape(s)}(?![a-z0-9-])", low)
            and s != gslug
        }
        goals.append(Goal(slug=gslug, title=title, mentions=mentions))
    return goals


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


# --------------------------------------------------------------------------- #
# the assembled hierarchy
# --------------------------------------------------------------------------- #
@dataclass
class Hierarchy:
    parent_of: dict[str, str] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    kind_of: dict[str, str] = field(default_factory=dict)   # goal|program|thread
    goal_titles: dict[str, str] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def kind(self, node: str, reg_slugs: set[str], goal_slugs: set[str]) -> str:
        if node in goal_slugs:
            return "goal"
        if node in reg_slugs:
            return "thread"
        return "program"

    def roots(self) -> list[str]:
        return [n for n in self.children_of if n not in self.parent_of]


def _detect_cycles(parent_of: dict[str, str]) -> None:
    for start in parent_of:
        seen = {start}
        node = start
        while node in parent_of:
            node = parent_of[node]
            if node in seen:
                chain = " → ".join(list(seen) + [node])
                raise HierarchyError(f"cycle in hierarchy.md: {chain}")
            seen.add(node)


def load_hierarchy(reg: registry.Registry | None = None) -> Hierarchy:
    """Load + validate hierarchy.md, then layer goal-derived roots underneath.

    Raises :class:`HierarchyError` on a cycle or a thread with two parents.
    """
    reg = reg or registry.load_registry()
    reg_slugs = set(reg.slugs)
    goals = load_goals(reg_slugs)
    goal_slugs = {g.slug for g in goals}

    h = Hierarchy()
    for g in goals:
        h.goal_titles[g.slug] = g.title

    # 1. explicit edges from hierarchy.md win, and enforce single-parent.
    path = config.hierarchy_path()
    raw = RawHierarchy([], {}, {}, [])
    if path.exists():
        try:
            raw = parse_hierarchy_md(path.read_text(errors="replace"))
        except OSError:
            raw = RawHierarchy([], {}, {}, [])
    h.notes.update(raw.notes)
    for parent, child in raw.edges:
        if child == parent:
            raise HierarchyError(f"'{child}' is listed as its own parent")
        existing = h.parent_of.get(child)
        if existing is not None and existing != parent:
            raise HierarchyError(
                f"'{child}' has two parents in hierarchy.md: "
                f"'{existing}' and '{parent}' (a thread has at most one parent)")
        if existing is None:
            h.parent_of[child] = parent
            h.children_of.setdefault(parent, []).append(child)
        h.children_of.setdefault(parent, h.children_of.get(parent, []))

    # 2. goal derivation: a mentioned thread with no explicit parent → the goal.
    for g in sorted(goals, key=lambda x: x.slug):
        for child in sorted(g.mentions):
            if child in h.parent_of:
                continue
            h.parent_of[child] = g.slug
            h.children_of.setdefault(g.slug, []).append(child)
        h.children_of.setdefault(g.slug, h.children_of.get(g.slug, []))

    _detect_cycles(h.parent_of)

    # kinds for every node that appears anywhere
    nodes = set(h.children_of) | set(h.parent_of)
    for n in nodes:
        h.kind_of[n] = h.kind(n, reg_slugs, goal_slugs)
    return h


# --------------------------------------------------------------------------- #
# roll-up tree (aggregate each node's subtree)
# --------------------------------------------------------------------------- #
@dataclass
class TreeNode:
    slug: str
    kind: str                                    # goal|program|thread|root
    children: list["TreeNode"] = field(default_factory=list)
    title: str = ""
    note: str = ""
    # own (leaf) stats — nonzero only for thread nodes that hold sessions
    own_sessions: int = 0
    own_sessions_in_window: int = 0
    own_last: datetime | None = None
    dormant: bool = False
    closed: bool = False
    # rolled-up over the whole subtree (own + descendants)
    sessions: int = 0
    sessions_in_window: int = 0
    last_activity: datetime | None = None
    relevance: float = 0.0


def _max_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def build_forest(hier: Hierarchy, stats_by_slug: dict[str, dict], *,
                 cfg: config.Config, now: datetime,
                 reg: registry.Registry | None = None) -> list[TreeNode]:
    """Build the roll-up forest. ``stats_by_slug`` maps a thread slug to
    ``{sessions, sessions_in_window, last, dormant, closed, title, note}``.

    Roots are goals and any unparented program; thread slugs with no parent are
    grouped under a synthetic ``(unparented)`` root. Every node's rolled-up
    stats and relevance are recomputed from the aggregated subtree.
    """
    goal_slugs = set(hier.goal_titles)
    # universe of thread nodes = those with stats (assigned sessions) ∪ those
    # named in the hierarchy.
    all_nodes = set(hier.children_of) | set(hier.parent_of) | set(stats_by_slug)

    def make(slug: str, seen: frozenset[str]) -> TreeNode:
        if slug in seen:  # defensive; load already rejects cycles
            return TreeNode(slug=slug, kind="program")
        kind = hier.kind_of.get(slug) or (
            "goal" if slug in goal_slugs else
            "thread" if slug in stats_by_slug else "program")
        st = stats_by_slug.get(slug, {})
        node = TreeNode(
            slug=slug, kind=kind,
            title=hier.goal_titles.get(slug, st.get("title", "")),
            note=hier.notes.get(slug, st.get("note", "")),
            own_sessions=int(st.get("sessions", 0)),
            own_sessions_in_window=int(st.get("sessions_in_window", 0)),
            own_last=st.get("last"),
            dormant=bool(st.get("dormant")),
            closed=bool(st.get("closed")),
        )
        for child in hier.children_of.get(slug, []):
            node.children.append(make(child, seen | {slug}))
        _rollup(node, cfg, now)
        return node

    forest: list[TreeNode] = []
    for root in sorted(hier.roots()):
        forest.append(make(root, frozenset()))

    # orphan threads: have stats, no parent, not already a root.
    placed = _covered(forest)
    orphans = sorted(
        s for s in stats_by_slug
        if s not in hier.parent_of and s not in placed)
    if orphans:
        root = TreeNode(slug=UNPARENTED, kind="root")
        for s in orphans:
            root.children.append(make(s, frozenset({UNPARENTED})))
        _rollup(root, cfg, now)
        forest.append(root)

    forest.sort(key=lambda n: n.relevance, reverse=True)
    return forest


def _covered(forest: list[TreeNode]) -> set[str]:
    out: set[str] = set()

    def walk(n: TreeNode):
        out.add(n.slug)
        for c in n.children:
            walk(c)

    for r in forest:
        walk(r)
    return out


def _rollup(node: TreeNode, cfg: config.Config, now: datetime) -> None:
    sessions = node.own_sessions
    in_window = node.own_sessions_in_window
    last = node.own_last
    for c in node.children:
        sessions += c.sessions
        in_window += c.sessions_in_window
        last = _max_dt(last, c.last_activity)
    node.sessions = sessions
    node.sessions_in_window = in_window
    node.last_activity = last
    days_since = None if last is None else max(
        0.0, (now - last).total_seconds() / 86400.0)
    node.relevance = relevance(
        ThreadStats(sessions_in_window=in_window, days_since_last_session=days_since),
        cfg)


# --------------------------------------------------------------------------- #
# auto-drafting program sections (called from weave's clustering step)
# --------------------------------------------------------------------------- #
_STOP_KEYWORDS = {
    "tool", "library", "experiment", "test", "tests", "fix", "bug", "repo",
    "refactor", "pipeline", "sprint", "poc", "wip", "the", "and", "for",
}


def _existing_sections(text: str) -> set[str]:
    return {_norm(m.group(2)) for m in (_SECTION.match(l) for l in text.splitlines()) if m}


def draft_programs(threads: list[dict], hier: Hierarchy, *,
                   min_siblings: int = 3) -> list[dict]:
    """Draft ``## program-…`` sections into hierarchy.md for clusters of >= 3
    *unparented* sibling threads sharing a repo or keyword.

    ``threads`` is ``[{slug, repos: set[str], keywords: set[str]}]``. Idempotent:
    a program section already present in hierarchy.md is never rewritten, and a
    thread joins at most one drafted program. Returns the drafted program dicts.
    """
    unparented = [t for t in threads if t["slug"] not in hier.parent_of]
    by_slug = {t["slug"]: t for t in unparented}

    path = config.hierarchy_path()
    existing_text = ""
    if path.exists():
        try:
            existing_text = path.read_text(errors="replace")
        except OSError:
            existing_text = ""
    taken_names = _existing_sections(existing_text)

    # candidate grouping tokens: repos first (strong), then keywords.
    def _groups(key: str, prefix: str) -> list[tuple[str, list[str]]]:
        token_members: dict[str, list[str]] = {}
        for t in unparented:
            for tok in t.get(key, set()):
                tok = _norm(tok)
                if not tok or (key == "keywords" and tok in _STOP_KEYWORDS):
                    continue
                token_members.setdefault(tok, []).append(t["slug"])
        out = []
        for tok, members in sorted(token_members.items()):
            if len(set(members)) >= min_siblings:
                out.append((f"{prefix}-{tok}", sorted(set(members))))
        return out

    candidates = _groups("repos", "program") + _groups("keywords", "program")

    drafted: list[dict] = []
    claimed: set[str] = set()
    new_sections: list[str] = []
    for name, members in candidates:
        members = [m for m in members if m not in claimed]
        if len(members) < min_siblings:
            continue
        if name in taken_names:
            claimed.update(members)  # respect an existing (possibly edited) section
            continue
        claimed.update(members)
        taken_names.add(name)
        new_sections.append(_render_section(name, members))
        drafted.append({"name": name, "members": members})
        # reflect into the in-memory hierarchy so callers see the new parent.
        for m in members:
            hier.parent_of.setdefault(m, name)
            hier.children_of.setdefault(name, []).append(m)
            hier.kind_of.setdefault(m, "thread")
        hier.kind_of[name] = "program"

    if new_sections:
        _append_sections(path, existing_text, new_sections)
    return drafted


def _render_section(name: str, members: list[str]) -> str:
    lines = [f"## program: {name}", "",
             f"_{AGENT_MARKER}_",
             f"Auto-drafted: {len(members)} sibling threads share this repo/keyword.",
             ""]
    lines += [f"- [[{m}]]" for m in members]
    return "\n".join(lines) + "\n"


def _append_sections(path, existing_text: str, sections: list[str]) -> None:
    config.ensure_spool()
    if not existing_text.strip():
        header = ("# threads hierarchy\n\n"
                  f"_{AGENT_MARKER}_\n\n"
                  "Parents are goals (from `jarvis/goals/`) or auto-drafted\n"
                  "programs. Edit or delete a section to override; the tool\n"
                  "never rewrites an existing section.\n\n")
        existing_text = header
    body = existing_text
    if not body.endswith("\n"):
        body += "\n"
    body += "\n" + "\n".join(sections)
    try:
        path.write_text(body)
    except OSError:
        pass
