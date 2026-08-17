"""Transcript parsing: triviality, malformed lines, hints, downsample cap."""

from __future__ import annotations

from threads.transcripts import PROMPT_CAP, parse_transcript

from conftest import NOW


def test_nontrivial_session(env):
    p = env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8, span_min=30)
    s = parse_transcript(p)
    assert s.session_id == "s1"
    assert s.n_messages == 16  # 8 pairs
    assert not s.trivial
    assert s.cwd == "/h/jarvis"
    assert s.git_branch == "main"


def test_trivial_by_message_count(env):
    p = env.add_transcript("proj", "s2", cwd="/h/jarvis", n_pairs=3, span_min=30)
    assert parse_transcript(p).trivial


def test_trivial_by_span(env):
    p = env.add_transcript("proj", "s3", cwd="/h/jarvis", n_pairs=8, span_min=2)
    assert parse_transcript(p).trivial


def test_malformed_lines_skipped_not_fatal(env):
    p = env.add_transcript("proj", "s4", cwd="/h/jarvis", n_pairs=8, malformed=True)
    s = parse_transcript(p)
    assert s.parse_warnings >= 1
    assert s.n_messages >= 16  # real messages still counted


def test_repo_hints_from_paths(env):
    p = env.add_transcript(
        "proj", "s5", cwd="/h/jarvis", n_pairs=8,
        tool_paths=["/h/jarvis/repos/safety-desert/src/a.py",
                    "/h/jarvis/repos/safety-desert/README.md"])
    s = parse_transcript(p)
    assert "safety-desert" in s.hints["repos"]


def test_stub_edit_hint(env, monkeypatch):
    # stub hints key on the literal ~/jarvis-memory path under the real HOME
    home = str(env.root)
    monkeypatch.setattr("threads.transcripts._HOME", home)
    p = env.add_transcript(
        "proj", "s6", cwd="/h/jarvis", n_pairs=8,
        tool_paths=[f"{home}/jarvis-memory/logit-interpolation.md"])
    s = parse_transcript(p)
    assert "logit-interpolation" in s.hints["stubs"]


def test_pr_and_repo_reference_in_text(env):
    p = env.add_transcript(
        "proj", "s7", cwd="/h/jarvis", n_pairs=8,
        extra_text="reviewing ArcadiaImpact/science-of-midtraining#251 now")
    s = parse_transcript(p)
    assert "science-of-midtraining" in s.hints["repos"]
    assert "ArcadiaImpact/science-of-midtraining#251" in s.hints["prs"]


def test_downsample_cap(env):
    big = "z" * 200_000
    p = env.add_transcript("proj", "s8", cwd="/h/jarvis", n_pairs=8, extra_text=big)
    s = parse_transcript(p)
    assert len(s.prompt_view) <= PROMPT_CAP + 100  # +elision marker


def test_concierge_detection(env):
    p = env.add_transcript(
        "proj", "s9", n_pairs=8,
        cwd="/h/concierge-home/workspaces/t-0817-abcd")
    assert parse_transcript(p).is_concierge


def test_self_reflection_excluded_from_discovery(env):
    from threads.transcripts import is_self_reflection, iter_transcript_paths
    from conftest import NOW
    real = env.add_transcript("proj", "real", cwd="/h/jarvis", n_pairs=8)
    refl = env.add_transcript(
        "proj", "refl", cwd="/h/jarvis", n_pairs=8,
        extra_text="", )
    # overwrite refl's first user turn with the summarizer preamble
    import json as _j
    lines = refl.read_text().splitlines()
    lines[0] = _j.dumps({"type": "user", "timestamp": NOW.isoformat(),
                         "message": {"role": "user", "content":
                                     "You are summarizing a Claude Code session "
                                     "transcript for an activity dashboard."}})
    refl.write_text("\n".join(lines) + "\n")
    import os
    os.utime(refl, (NOW.timestamp(), NOW.timestamp()))
    assert is_self_reflection(refl)
    assert not is_self_reflection(real)
    found = {p.stem for p in iter_transcript_paths(30, now=NOW)}
    assert "real" in found and "refl" not in found
