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
