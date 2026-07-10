"""The shipped guard asset must actually block the anti-patterns it claims to."""
import json
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "concierge" / "assets" / "guard_background_tasks.py"


def _run(payload: dict):
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True)


def _bash(command: str, run_in_background: bool = False) -> dict:
    return {"tool_name": "Bash",
            "tool_input": {"command": command, "run_in_background": run_in_background}}


def test_nohup_in_background_is_blocked():
    r = _run(_bash("nohup foo &", run_in_background=True))
    assert r.returncode == 2
    assert "nohup" in r.stderr


def test_trailing_ampersand_in_background_is_blocked():
    r = _run(_bash("python train.py & echo launched", run_in_background=True))
    assert r.returncode == 2


def test_plain_command_is_allowed():
    r = _run(_bash("python train.py", run_in_background=True))
    assert r.returncode == 0
    assert r.stderr == ""


def test_pgrep_watcher_loop_is_blocked():
    r = _run(_bash("while pgrep -f train.py; do sleep 5; done"))
    assert r.returncode == 2


def test_nohup_without_background_is_allowed():
    # the detach guards only fire for run_in_background commands
    r = _run(_bash("nohup foo &", run_in_background=False))
    assert r.returncode == 0


def test_non_bash_tool_is_ignored():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert r.returncode == 0
