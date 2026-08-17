"""weave: each pass in priority order, fallback validation, concierge
resolution, clustering, and weave --check thresholds."""

from __future__ import annotations

import json

from threads import config, spool, weave
from threads.registry import load_registry

from conftest import NOW

CONC_CWD = "/h/concierge-home/workspaces/t-0817-abcd"


def _assign(rec):
    return weave.assign_one(rec, load_registry())


def test_stub_edit_pass(env, write_record):
    env.add_stub("logit-interpolation")
    rec = write_record("s", hints={"stubs": ["logit-interpolation"], "goals": [],
                                   "repos": ["safety-desert"], "branches": [],
                                   "prs": []})
    assert _assign(rec) == ("logit-interpolation", "stub-edit")  # beats repo


def test_goals_pass(env, write_record):
    env.add_stub("self-driving-jarvis")
    rec = write_record("s", hints={"stubs": [], "goals": ["self-driving-jarvis"],
                                   "repos": [], "branches": [], "prs": []})
    assert _assign(rec) == ("self-driving-jarvis", "goals")


def test_repo_pass_exact(env, write_record):
    env.add_stub("safety-desert", body="clone repos/safety-desert")
    rec = write_record("s", hints={"stubs": [], "goals": [],
                                   "repos": ["safety-desert"], "branches": [],
                                   "prs": []})
    assert _assign(rec) == ("safety-desert", "repo")


def test_repo_pass_via_stub_mention(env, write_record):
    env.add_stub("phd-thesis-psm-program", body="work lives in ~/phd-thesis dir")
    rec = write_record("s", hints={"stubs": [], "goals": [],
                                   "repos": ["phd-thesis"], "branches": [],
                                   "prs": []})
    assert _assign(rec) == ("phd-thesis-psm-program", "repo")


def test_branch_pass_normalized_substring(env, write_record):
    env.add_stub("runpod-availability-benchmark")
    rec = write_record("s", hints={"stubs": [], "goals": [], "repos": [],
                                   "branches": ["runpod-availability"], "prs": []})
    assert _assign(rec) == ("runpod-availability-benchmark", "branch")


def test_model_fallback_only_if_slug_exists(env, write_record):
    env.add_stub("arsenal-monorepo")
    ok = write_record("a", candidate_slugs=["arsenal-monorepo"])
    assert _assign(ok) == ("arsenal-monorepo", "model")
    bad = write_record("b", candidate_slugs=["does-not-exist"])
    assert _assign(bad) == (None, "unfiled")


def test_concierge_matches_task_repo(env, write_record):
    env.add_stub("safety-desert", body="repos/safety-desert")
    env.add_task("t-0817-abcd", title="safety desert experiment",
                 repo="dtch1997/safety-desert")
    rec = write_record("s", cwd=CONC_CWD, is_concierge=True)
    assert _assign(rec) == ("safety-desert", "concierge")


def test_concierge_unmatched_goes_to_review_bucket(env, write_record):
    env.add_task("t-0817-abcd", title="totally unrelated widget task")
    rec = write_record("s", cwd=CONC_CWD, is_concierge=True)
    assert _assign(rec) == (None, "concierge-review")


def test_weave_writes_assignments_and_report(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    write_record("s2", candidate_slugs=["nope"])  # unfiled
    res = weave.weave(runner=canned_runner, cluster=False)
    assert res.total == 2
    assert res.matched == 1
    rows = spool.load_assignments()
    assert {r["session_id"] for r in rows} == {"s1", "s2"}


def test_clustering_drafts_candidates(env, write_record):
    r1 = write_record("u1", title="poking at foo", keywords=["foo"])
    r2 = write_record("u2", title="more foo", keywords=["foo"])

    def runner(prompt, *, model):
        return {"text": json.dumps({"clusters": [
            {"name": "Foo work", "note": "shared foo investigation.",
             "members": ["u1", "u2"]}]}), "cost_usd": 0.0}

    clusters = weave.cluster_unfiled([r1, r2], runner=runner)
    assert len(clusters) == 1
    files = list(config.candidates_dir().glob("*.md"))
    assert files
    body = files[0].read_text()
    assert "agent-drafted, standing until Daniel edits" in body


def test_clustering_drops_singletons(env, write_record):
    r1 = write_record("u1", title="a", keywords=["a"])
    r2 = write_record("u2", title="b", keywords=["b"])

    def runner(prompt, *, model):
        return {"text": json.dumps({"clusters": [
            {"name": "lonely", "note": "x", "members": ["u1"]}]}), "cost_usd": 0.0}

    assert weave.cluster_unfiled([r1, r2], runner=runner) == []


def test_weave_check_passes_at_high_rate(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    for i in range(3):
        write_record(f"s{i}", hints={"stubs": [], "goals": [],
                                     "repos": ["safety-desert"], "branches": [],
                                     "prs": []})
    weave.weave(runner=canned_runner, cluster=False)
    ok, report = weave.weave_check()
    assert ok, report
    assert "100%" in report


def test_weave_check_fails_below_threshold(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s0", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    for i in range(3):
        write_record(f"u{i}", candidate_slugs=["nope"])  # unfiled, non-trivial
    weave.weave(runner=canned_runner, cluster=False)
    ok, report = weave.weave_check()
    assert not ok
    assert "25%" in report


def test_weave_check_requires_concierge_resolution(env, write_record, canned_runner):
    # a concierge session with a missing task file -> review bucket -> resolved
    write_record("c1", cwd=CONC_CWD, is_concierge=True)
    weave.weave(runner=canned_runner, cluster=False)
    ok, report = weave.weave_check()
    assert ok, report
    assert "concierge sessions resolved: 1/1" in report


def test_trivial_sessions_excluded_from_rate(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s0", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    write_record("t0", trivial=True, candidate_slugs=[])  # unfiled but trivial
    weave.weave(runner=canned_runner, cluster=False)
    ok, report = weave.weave_check()
    assert ok, report  # trivial unfiled must not drag the rate down
