"""note/pickup: the deliberate push channel (no model, no serving)."""

from __future__ import annotations

import io

import pytest

from threads import config, dashboard, note
from threads.cli import main

from conftest import NOW


def test_add_note_writes_frontmatter_and_body(env):
    p = note.add_note("safety-desert", "Parked: figures half done.\n\nNext: blogpost.",
                      status="parked", cwd=str(env.root), now=NOW)
    assert p.parent == config.notes_dir() / "safety-desert"
    text = p.read_text()
    assert text.startswith("---\n")
    assert "status: parked" in text
    assert "Next: blogpost." in text
    recs = note.load_notes("safety-desert")
    assert len(recs) == 1
    assert recs[0]["title"] == "Parked: figures half done."
    assert recs[0]["status"] == "parked"
    assert recs[0]["created"].startswith("2026-08-17")


def test_add_note_rejects_bad_input(env):
    with pytest.raises(ValueError):
        note.add_note("has space", "x", now=NOW)
    with pytest.raises(ValueError):
        note.add_note("ok-slug", "   ", now=NOW)


def test_same_second_notes_do_not_collide(env):
    a = note.add_note("s", "first", cwd=str(env.root), now=NOW)
    b = note.add_note("s", "second", cwd=str(env.root), now=NOW)
    assert a != b
    assert len(note.load_notes("s")) == 2


def test_notes_are_newest_first(env):
    from datetime import timedelta
    note.add_note("s", "old", cwd=str(env.root), now=NOW - timedelta(days=2))
    note.add_note("s", "new", cwd=str(env.root), now=NOW)
    bodies = [n["body"] for n in note.load_notes("s")]
    assert bodies == ["new", "old"]


def test_dashboard_shows_note_only_thread_as_new(env):
    note.add_note("brand-new-idea", "seed it", cwd=str(env.root), now=NOW)
    dash = dashboard.build(now=NOW)
    t = {t.slug: t for t in dash.threads}["brand-new-idea"]
    assert not t.registered and t.count == 0 and len(t.notes) == 1
    md = dashboard.render_markdown(dash)
    assert "brand-new-idea" in md and "(new)" in md and "📌1" in md
    html = dashboard.render_html(dash)
    assert "seed it" in html


def test_note_refreshes_dormancy(env, write_record):
    from datetime import timedelta
    from threads import spool
    env.add_stub("safety-desert", memory_line="next = figures")
    write_record("s1", t_end=NOW - timedelta(days=20))
    spool.write_assignments([{"session_id": "s1", "slug": "safety-desert",
                              "method": "repo", "confidence": "high"}])
    assert dashboard.build(now=NOW).threads[0].dormant
    note.add_note("safety-desert", "still on it", cwd=str(env.root), now=NOW)
    assert not dashboard.build(now=NOW).threads[0].dormant


def test_pickup_renders_notes_and_sessions(env, write_record):
    from threads import spool
    env.add_stub("safety-desert", memory_line="verdict = drain yes")
    write_record("s1", title="ran the evals")
    spool.write_assignments([{"session_id": "s1", "slug": "safety-desert",
                              "method": "repo", "confidence": "high"}])
    note.add_note("safety-desert", "Next: blogpost figures.", status="parked",
                  cwd=str(env.root), now=NOW)
    md = dashboard.render_pickup("safety-desert")
    assert "pickup: safety-desert" in md
    assert "verdict = drain yes" in md
    assert "Next: blogpost figures." in md
    assert "ran the evals" in md
    # notes come before observed sessions
    assert md.index("Next: blogpost figures.") < md.index("ran the evals")


def test_cli_note_and_pickup(env, capsys, monkeypatch):
    env.add_stub("safety-desert")
    assert main(["note", "safety-desert", "parking", "this", "here"]) == 0
    out = capsys.readouterr().out
    assert "noted →" in out and "threads pickup safety-desert" in out
    assert main(["pickup", "safety-desert"]) == 0
    assert "parking this here" in capsys.readouterr().out


def test_cli_note_stdin_body(env, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("## Dump\nlong context here\n"))
    assert main(["note", "new-thing", "-", "--status", "parked"]) == 0
    out = capsys.readouterr().out
    assert "candidate thread" in out
    assert note.load_notes("new-thing")[0]["body"].startswith("## Dump")


def test_cli_note_bad_slug_errors(env, capsys):
    assert main(["note", "bad/slug", "x"]) == 1
