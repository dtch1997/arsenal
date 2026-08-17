"""scan: idempotent keying, triviality stubs, call cap + flare, scan --check."""

from __future__ import annotations

import json

import flare
from threads import scan, spool

from conftest import NOW


def test_summarizes_nontrivial_once(env, canned_runner):
    env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    res = scan.scan(days=30, runner=canned_runner, now=NOW)
    assert res.summarized == 1
    assert canned_runner.calls["n"] == 1
    rec = spool.load_summary("s1")
    assert rec["method"] == "model"
    assert rec["title"] == "did a thing"


def test_idempotent_rescan_is_noop(env, canned_runner):
    env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    res2 = scan.scan(days=30, runner=canned_runner, now=NOW)
    assert res2.skipped_unchanged == 1
    assert res2.model_calls == 0
    assert canned_runner.calls["n"] == 1  # no second call


def test_grown_transcript_is_resummarized(env, canned_runner):
    p = env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    # grow it
    with open(p, "a") as f:
        f.write(json.dumps({"type": "assistant", "sessionId": "s1",
                            "timestamp": NOW.isoformat(),
                            "message": {"role": "assistant",
                                        "content": [{"type": "text", "text": "more"}]}})
                + "\n")
    import os
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    res = scan.scan(days=30, runner=canned_runner, now=NOW)
    assert res.summarized == 1
    assert canned_runner.calls["n"] == 2


def test_trivial_gets_stub_no_model_call(env, canned_runner):
    env.add_transcript("proj", "triv", cwd="/h/jarvis", n_pairs=3)
    res = scan.scan(days=30, runner=canned_runner, now=NOW)
    assert res.trivial == 1
    assert canned_runner.calls["n"] == 0
    rec = spool.load_summary("triv")
    assert rec["method"] == "stub"
    assert rec["title"].startswith("user turn 0")


def test_call_cap_truncates_and_flares(env, canned_runner, monkeypatch):
    sent = []
    monkeypatch.setattr(flare, "send", lambda *a, **k: sent.append((a, k)))
    for i in range(5):
        env.add_transcript("proj", f"s{i}", cwd="/h/jarvis", n_pairs=8)
    res = scan.scan(days=30, max_calls=2, runner=canned_runner, now=NOW)
    assert res.model_calls == 2
    assert res.truncated is True
    assert sent and sent[0][1].get("source") == "threads"


def test_all_flag_lifts_cap(env, canned_runner):
    for i in range(5):
        env.add_transcript("proj", f"s{i}", cwd="/h/jarvis", n_pairs=8)
    res = scan.scan(days=30, max_calls=2, all_=True, runner=canned_runner, now=NOW)
    assert res.model_calls == 5
    assert res.truncated is False


def test_state_records_spend_and_manifest(env, canned_runner):
    env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    state = spool.load_state()
    assert state["lookback_days"] == 30
    assert state["model_calls"] == 1
    assert state["est_spend_usd"] > 0
    assert "s1" in state["manifest"]


def test_scan_check_passes_after_full_scan(env, canned_runner):
    env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    env.add_transcript("proj", "triv", cwd="/h/jarvis", n_pairs=3)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    ok, report = scan.scan_check(days=30, now=NOW)
    assert ok, report


def test_scan_check_fails_without_scan(env):
    ok, report = scan.scan_check(days=30, now=NOW)
    assert not ok
    assert "no scan" in report


def test_scan_check_tolerates_grown_model_session(env, canned_runner):
    # a live non-trivial session keeps appending; its model summary already
    # exists, so --check treats the drift as refresh-only (stays green).
    p = env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    with open(p, "a") as f:
        f.write("\n" + json.dumps({"type": "user", "sessionId": "s1",
                                    "timestamp": NOW.isoformat(),
                                    "message": {"role": "user", "content": "hi"}}))
    import os
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    ok, report = scan.scan_check(days=30, now=NOW)
    assert ok, report
    assert "grown since scan" in report


def test_scan_check_fails_when_stub_grows(env, canned_runner):
    # a trivial (stub) session that grew might have crossed into non-trivial:
    # a re-scan would do real work, so --check must fail.
    p = env.add_transcript("proj", "triv", cwd="/h/jarvis", n_pairs=3)
    scan.scan(days=30, runner=canned_runner, now=NOW)
    with open(p, "a") as f:
        f.write("\n" + json.dumps({"type": "user", "sessionId": "triv",
                                    "timestamp": NOW.isoformat(),
                                    "message": {"role": "user", "content": "hi"}}))
    import os
    os.utime(p, (NOW.timestamp(), NOW.timestamp()))
    ok, report = scan.scan_check(days=30, now=NOW)
    assert not ok
    assert "grew" in report


def test_scan_check_fails_when_truncated_leaves_gap(env, canned_runner):
    for i in range(3):
        env.add_transcript("proj", f"s{i}", cwd="/h/jarvis", n_pairs=8)
    scan.scan(days=30, max_calls=1, runner=canned_runner, now=NOW)
    ok, report = scan.scan_check(days=30, now=NOW)
    assert not ok
