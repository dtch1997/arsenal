import subprocess
import sys

from statusline.render import GREEN, RED, RESET, YELLOW, render


def payload(remaining=75.0, model="Opus", cost=0.1234):
    return {
        "model": {"display_name": model},
        "context_window": {"remaining_percentage": remaining},
        "cost": {"total_cost_usd": cost},
    }


def test_full_line_matches_legacy_bash_format():
    # 75% of a 15-wide bar → 11 filled cells (round(75*15/100) = 11)
    assert render(payload()) == (
        f"{GREEN}[Opus] Context: 75% [███████████░░░░] | $0.123{RESET}"
    )


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


def test_cli_survives_garbage_stdin():
    out = subprocess.run(
        [sys.executable, "-m", "statusline.cli"],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0
    assert out.stdout == "[Claude] Context: --%"
