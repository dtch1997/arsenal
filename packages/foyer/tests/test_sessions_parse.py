"""Parsing regression: tmux >=3.4 vis-escapes the \\x1f field separator.

The e2e tests only catch this when they run under a tmux that escapes (3.4+);
on an older dev box (3.2a emits the raw byte) they stay green even if the
escaped-form handling regresses. This exercises the parser directly, with no
tmux, so it guards the regression everywhere.
"""
from __future__ import annotations

from foyer import sessions

_FIELD_VALUES = [
    "foyer-pytest",  # session_name
    "1786367355",    # session_created
    "0",             # session_attached
    "1786367355",    # session_activity
    "1",             # window_active
    "1",             # pane_active
    "/tmp/work",     # pane_current_path
    "bash",          # pane_current_command
    "some-title",    # pane_title
]


def _run_with_line(monkeypatch, line: str) -> list[dict]:
    monkeypatch.setattr(sessions, "_tmux", lambda *a: line + "\n")
    return sessions.list_sessions(with_preview=False)


def test_parses_raw_separator(monkeypatch):
    """tmux <=3.2a emits the raw \\x1f byte between fields."""
    rows = _run_with_line(monkeypatch, sessions._SEP.join(_FIELD_VALUES))
    assert [r["name"] for r in rows] == ["foyer-pytest"]
    assert rows[0]["cwd"] == "/tmp/work"
    assert rows[0]["command"] == "bash"


def test_parses_octal_escaped_separator(monkeypatch):
    """tmux >=3.4 emits the separator as the literal octal escape ``\\037``."""
    rows = _run_with_line(monkeypatch, r"\037".join(_FIELD_VALUES))
    assert [r["name"] for r in rows] == ["foyer-pytest"]
    assert rows[0]["cwd"] == "/tmp/work"
    assert rows[0]["command"] == "bash"
