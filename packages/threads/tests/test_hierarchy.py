"""hierarchy: parse, goal-root derivation, cycle/single-parent rejection,
roll-up aggregation, and auto-drafted program sections."""

from __future__ import annotations

from datetime import timedelta

import pytest

from threads import config, hierarchy
from threads.registry import load_registry

from conftest import NOW


def _reg(env, *slugs):
    for s in slugs:
        env.add_stub(s)
    return load_registry()


def test_parse_sections_and_wikilinks(env):
    env.write_hierarchy(
        "# threads hierarchy\n\n"
        "## program: arch2-work\n"
        "some note here\n"
        "- [[arch2-tooling-bugs]]\n"
        "- [[durable-organisms-arch2-sprint2]]\n")
    raw = hierarchy.parse_hierarchy_md(config.hierarchy_path().read_text())
    assert ("arch2-work", "arch2-tooling-bugs") in raw.edges
    assert raw.notes["arch2-work"] == "some note here"
    assert raw.declared_kind["arch2-work"] == "program"


def test_goal_root_derivation_from_fixture_goals(env):
    reg = _reg(env, "dogfight-rl", "arch2-tooling-bugs")
    env.add_goal("dogfight-rl-release", title="Dogfight RL",
                 mentions=["dogfight-rl"])
    hier = hierarchy.load_hierarchy(reg)
    assert hier.parent_of["dogfight-rl"] == "dogfight-rl-release"
    assert hier.kind_of["dogfight-rl-release"] == "goal"
    assert hier.kind_of["dogfight-rl"] == "thread"
    # a thread not mentioned by any goal stays unparented
    assert "arch2-tooling-bugs" not in hier.parent_of


def test_hierarchy_md_overrides_goal_derivation(env):
    reg = _reg(env, "dogfight-rl")
    env.add_goal("dogfight-rl-release", mentions=["dogfight-rl"])
    env.write_hierarchy("## program: rl-infra\n- [[dogfight-rl]]\n")
    hier = hierarchy.load_hierarchy(reg)
    assert hier.parent_of["dogfight-rl"] == "rl-infra"  # explicit beats goal


def test_cycle_rejected(env):
    reg = _reg(env, "a-thread", "b-thread")
    env.write_hierarchy("## a-thread\n- [[b-thread]]\n\n## b-thread\n- [[a-thread]]\n")
    with pytest.raises(hierarchy.HierarchyError, match="cycle"):
        hierarchy.load_hierarchy(reg)


def test_single_parent_enforced(env):
    reg = _reg(env, "child-thread")
    env.write_hierarchy(
        "## parent-one\n- [[child-thread]]\n\n## parent-two\n- [[child-thread]]\n")
    with pytest.raises(hierarchy.HierarchyError, match="two parents"):
        hierarchy.load_hierarchy(reg)


def test_rollup_aggregates_subtree(env):
    reg = _reg(env, "leaf-a", "leaf-b")
    env.write_hierarchy("## program: proj\n- [[leaf-a]]\n- [[leaf-b]]\n")
    hier = hierarchy.load_hierarchy(reg)
    stats = {
        "leaf-a": {"sessions": 3, "sessions_in_window": 3,
                   "last": NOW - timedelta(days=1)},
        "leaf-b": {"sessions": 2, "sessions_in_window": 1,
                   "last": NOW - timedelta(days=10)},
    }
    forest = hierarchy.build_forest(hier, stats, cfg=config.Config(), now=NOW)
    root = next(n for n in forest if n.slug == "proj")
    assert root.sessions == 5
    assert root.sessions_in_window == 4
    assert root.last_activity == NOW - timedelta(days=1)  # max of children
    # relevance recomputed from aggregated stats, same formula
    from threads.relevance import ThreadStats, relevance
    want = relevance(ThreadStats(4, 1.0), config.Config())
    assert abs(root.relevance - want) < 1e-9


def test_orphans_grouped_under_unparented(env):
    reg = _reg(env, "lonely")
    hier = hierarchy.load_hierarchy(reg)
    stats = {"lonely": {"sessions": 1, "sessions_in_window": 1, "last": NOW}}
    forest = hierarchy.build_forest(hier, stats, cfg=config.Config(), now=NOW)
    roots = {n.slug for n in forest}
    assert hierarchy.UNPARENTED in roots
    unp = next(n for n in forest if n.slug == hierarchy.UNPARENTED)
    assert [c.slug for c in unp.children] == ["lonely"]


def test_draft_programs_appends_section(env):
    reg = _reg(env, "t1", "t2", "t3", "t4")
    hier = hierarchy.load_hierarchy(reg)
    threads = [
        {"slug": "t1", "repos": {"arch2"}, "keywords": set()},
        {"slug": "t2", "repos": {"arch2"}, "keywords": set()},
        {"slug": "t3", "repos": {"arch2"}, "keywords": set()},
        {"slug": "t4", "repos": {"other"}, "keywords": set()},
    ]
    drafted = hierarchy.draft_programs(threads, hier, min_siblings=3)
    assert len(drafted) == 1
    assert drafted[0]["name"] == "program-arch2"
    assert set(drafted[0]["members"]) == {"t1", "t2", "t3"}
    text = config.hierarchy_path().read_text()
    assert "program: program-arch2" in text
    assert hierarchy.AGENT_MARKER in text
    # in-memory hierarchy reflects the new parent
    assert hier.parent_of["t1"] == "program-arch2"


def test_draft_programs_idempotent(env):
    reg = _reg(env, "t1", "t2", "t3")
    threads = [{"slug": s, "repos": {"arch2"}, "keywords": set()}
               for s in ("t1", "t2", "t3")]
    d1 = hierarchy.draft_programs(threads, hierarchy.load_hierarchy(reg), min_siblings=3)
    assert len(d1) == 1
    # second run over a hierarchy that already has the section drafts nothing
    d2 = hierarchy.draft_programs(threads, hierarchy.load_hierarchy(reg), min_siblings=3)
    assert d2 == []
    assert config.hierarchy_path().read_text().count("program-arch2") <= 4


def test_draft_programs_skips_parented_threads(env):
    reg = _reg(env, "t1", "t2", "t3")
    env.add_goal("some-goal", mentions=["t1", "t2", "t3"])
    hier = hierarchy.load_hierarchy(reg)  # all three already under the goal
    threads = [{"slug": s, "repos": {"arch2"}, "keywords": set()}
               for s in ("t1", "t2", "t3")]
    assert hierarchy.draft_programs(threads, hier, min_siblings=3) == []
