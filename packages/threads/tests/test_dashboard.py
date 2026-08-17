"""dashboard: build model, markdown + html shape, sparkline, dormancy."""

from __future__ import annotations

from datetime import timedelta

from threads import dashboard, spool, weave
from threads.registry import load_registry

from conftest import NOW


def _seed(env, write_record, canned_runner, **kw):
    weave.weave(runner=canned_runner, cluster=False)


def test_build_groups_by_slug_and_flags_dormant(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert",
                 memory_line="active experiment")
    env.add_stub("logit-interpolation", body="repos/logit-interpolation",
                 memory_line="COMPLETE and wrapped up")
    # recent activity on safety-desert
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []}, t_end=NOW)
    # old activity on logit-interpolation (but its line reads complete)
    write_record("l1", hints={"stubs": ["logit-interpolation"], "goals": [],
                              "repos": [], "branches": [], "prs": []},
                 t_end=NOW - timedelta(days=40))
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    by = {t.slug: t for t in dash.threads}
    assert not by["safety-desert"].dormant
    # closed line suppresses the dormancy flag even though 40d old
    assert by["logit-interpolation"].closed
    assert not by["logit-interpolation"].dormant


def test_dormant_flag_when_old_and_open(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert",
                 memory_line="still going")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []},
                 t_end=NOW - timedelta(days=30))
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    assert dash.threads[0].dormant


def test_markdown_shape(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    write_record("u1", candidate_slugs=["nope"])
    weave.weave(runner=canned_runner, cluster=False)
    md = dashboard.render_markdown(now=NOW)
    assert "# threads — activity dashboard" in md
    assert "## Threads" in md
    assert "## Unfiled inbox" in md
    assert "## Candidate threads" in md
    assert "safety-desert" in md


def test_html_shape_and_escaping(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []},
                 title="<script>alert(1)</script>")
    weave.weave(runner=canned_runner, cluster=False)
    h = dashboard.render_html(now=NOW)
    assert "<!doctype html>" in h
    assert "Thread drill-down" in h
    assert "<script>alert(1)" not in h  # escaped
    assert "&lt;script&gt;" in h


def test_sparkline_length(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    assert len(dash.threads[0].sparkline(NOW, days=30)) == 30


def test_relevance_computed_and_sorts_table(env, write_record, canned_runner):
    env.add_stub("hot-thread", body="repos/hot-thread")
    env.add_stub("cold-thread", body="repos/cold-thread")
    write_record("h1", hints={"stubs": [], "goals": [], "repos": ["hot-thread"],
                              "branches": [], "prs": []}, t_end=NOW)
    write_record("c1", hints={"stubs": [], "goals": [], "repos": ["cold-thread"],
                              "branches": [], "prs": []},
                 t_end=NOW - timedelta(days=25))
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    by = {t.slug: t for t in dash.threads}
    assert by["hot-thread"].relevance > by["cold-thread"].relevance
    # default table sort is by relevance, descending
    assert dash.threads[0].slug == "hot-thread"


def test_tree_view_and_coverage(env, write_record, canned_runner):
    env.add_stub("dogfight-rl")
    env.add_stub("orphan-thread", body="repos/orphan-thread")
    env.add_goal("dogfight-rl-release", mentions=["dogfight-rl"])
    write_record("d1", hints={"stubs": ["dogfight-rl"], "goals": [], "repos": [],
                              "branches": [], "prs": []}, t_end=NOW)
    write_record("o1", hints={"stubs": [], "goals": [], "repos": ["orphan-thread"],
                              "branches": [], "prs": []}, t_end=NOW)
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    roots = {n.slug: n for n in dash.forest}
    assert "dogfight-rl-release" in roots
    goal = roots["dogfight-rl-release"]
    assert goal.kind == "goal" and goal.sessions == 1
    # orphan thread grouped under the synthetic unparented root
    from threads.hierarchy import UNPARENTED
    assert UNPARENTED in roots
    cov = dash.coverage()
    assert "orphan-thread" in cov["threads_no_goal"]
    assert "dogfight-rl" not in cov["threads_no_goal"]
    html = dashboard.render_html(dash)
    assert "Tree view" in html and "Coverage" in html


def test_broken_hierarchy_does_not_blank_dashboard(env, write_record, canned_runner):
    env.add_stub("a-thread", body="repos/a-thread")
    env.write_hierarchy("## p1\n- [[x]]\n\n## p2\n- [[x]]\n")  # x has two parents
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["a-thread"],
                              "branches": [], "prs": []})
    weave.weave(runner=canned_runner, cluster=False)
    dash = dashboard.build(now=NOW)
    assert dash.hierarchy_error  # surfaced, not raised
    assert dash.threads  # table still renders
    assert "hierarchy.md ignored" in dashboard.render_html(dash)
