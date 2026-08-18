import json
import subprocess
import sys

import pytest

from statusline.render import BOLD, DIM, GREEN, RED, RESET, YELLOW, render


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STATUSLINE_STATE_DIR", str(tmp_path))
    import statusline.session as session

    monkeypatch.setattr(session, "STATE_DIR", tmp_path)
    return tmp_path


def payload(remaining=75.0, model="Opus", cost=0.1234, **extra):
    return {
        "model": {"display_name": model},
        "context_window": {"remaining_percentage": remaining},
        "cost": {"total_cost_usd": cost},
        **extra,
    }


def test_base_line_content_and_tint():
    # 75% of a 15-wide bar → 11 filled cells (round(75*15/100) = 11)
    out = render(payload())
    assert out == f"{GREEN}[Opus] Context: 75% [███████████░░░░] | $0.123{RESET}"


def test_color_thresholds():
    assert render(payload(remaining=61.0)).startswith(GREEN)
    assert render(payload(remaining=60.0)).startswith(YELLOW)
    assert render(payload(remaining=30.0)).startswith(RED)


def test_no_context_data_yet():
    assert render({"model": {"display_name": "Opus"}}) == "[Opus] Context: --%"
    assert render({}) == "[Claude] Context: --%"


def test_bar_extremes():
    assert "[███████████████]" in render(payload(remaining=100.0))
    assert "[░░░░░░░░░░░░░░░]" in render(payload(remaining=0.0))


def test_missing_cost_defaults_to_zero():
    p = payload()
    del p["cost"]
    assert "| $0.000" in render(p)


def test_session_name_shown_as_topic_on_own_line():
    out = render(payload(session_name="Fix the flux capacitor"))
    assert out.splitlines()[1] == f"{DIM}· Fix the flux capacitor{RESET}"


def test_long_topic_truncated():
    out = render(payload(session_name="x" * 100))
    assert "x" * 79 + "…" in out
    assert "x" * 80 not in out


def test_sidecar_topic_overrides_session_name(state_dir):
    (state_dir / "sid1.json").write_text(json.dumps({"topic": "manual topic"}))
    out = render(payload(session_id="sid1", session_name="auto name"))
    assert "manual topic" in out
    assert "auto name" not in out


def test_flags_render_bold_red_on_last_line(state_dir):
    (state_dir / "sid1.json").write_text(json.dumps({"flags": ["open PR", "push branch"]}))
    out = render(payload(session_id="sid1", session_name="topic"))
    assert out.splitlines()[-1] == f"{BOLD}{RED}⚑ open PR; push branch{RESET}"


def test_empty_rows_dropped(state_dir):
    # no topic, no flags -> single line
    assert len(render(payload()).splitlines()) == 1
    # flags but no topic -> flags line directly after vitals
    (state_dir / "sid2.json").write_text(json.dumps({"flags": ["x"]}))
    assert len(render(payload(session_id="sid2")).splitlines()) == 2


def test_corrupt_sidecar_ignored(state_dir):
    (state_dir / "sid1.json").write_text("not json")
    out = render(payload(session_id="sid1", session_name="auto name"))
    assert "auto name" in out


def _cli(*argv, env_extra=None, stdin=""):
    import os

    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "statusline.cli", *argv],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_survives_garbage_stdin():
    out = _cli(stdin="not json")
    assert out.returncode == 0
    assert out.stdout == "[Claude] Context: --%"


def test_cli_flag_note_unflag_roundtrip(state_dir):
    env = {"STATUSLINE_STATE_DIR": str(state_dir), "CLAUDE_CODE_SESSION_ID": "sid9"}
    assert _cli("flag", "open PR", "push branch", env_extra=env).returncode == 0
    assert _cli("note", "my topic", env_extra=env).returncode == 0
    state = json.loads((state_dir / "sid9.json").read_text())
    assert state == {"flags": ["open PR", "push branch"], "topic": "my topic"}
    assert _cli("unflag", "pr", env_extra=env).returncode == 0
    state = json.loads((state_dir / "sid9.json").read_text())
    assert state["flags"] == ["push branch"]


def test_cli_no_session_id_errors():
    env = {"CLAUDE_CODE_SESSION_ID": ""}
    out = _cli("flag", "x", env_extra=env)
    assert out.returncode != 0
