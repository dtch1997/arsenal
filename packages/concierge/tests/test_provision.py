"""Workspace provisioning: guard hook installed, settings merged not clobbered."""
import json
import subprocess
from pathlib import Path

import pytest

from concierge import provision

HOOK_CMD = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/guard_background_tasks.py"'


def _bash_hooks(settings: dict) -> list:
    for group in settings["hooks"]["PreToolUse"]:
        if group.get("matcher") == "Bash":
            return group["hooks"]
    return []


def test_hook_script_and_settings_written(tmp_path):
    provision.install_guard_hook(tmp_path)
    hook = tmp_path / ".claude" / "hooks" / "guard_background_tasks.py"
    assert hook.exists()
    # copied verbatim from the package asset
    assert hook.read_text() == provision.GUARD_ASSET.read_text()

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for h in _bash_hooks(settings)]
    assert HOOK_CMD in cmds


def test_merge_preserves_existing_hooks(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "python3 repo-own-guard.py"}]},
            ]
        },
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    (claude / "settings.json").write_text(json.dumps(existing))

    provision.install_guard_hook(tmp_path)

    settings = json.loads((claude / "settings.json").read_text())
    cmds = [h["command"] for h in _bash_hooks(settings)]
    assert "python3 repo-own-guard.py" in cmds  # not clobbered
    assert HOOK_CMD in cmds                       # ours merged in
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}  # untouched


def test_install_is_idempotent(tmp_path):
    provision.install_guard_hook(tmp_path)
    provision.install_guard_hook(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for h in _bash_hooks(settings)]
    assert cmds.count(HOOK_CMD) == 1


def _git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def test_untracked_additions_are_git_excluded(git_repo):
    provision.install_guard_hook(git_repo)
    status = _git(git_repo, "status", "--porcelain").stdout
    assert "guard_background_tasks.py" not in status
    assert ".claude/settings.json" not in status


def test_tracked_settings_are_skip_worktreed(git_repo):
    claude = git_repo / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"hooks": {}}))
    _git(git_repo, "add", ".claude/settings.json")
    _git(git_repo, "commit", "-qm", "repo settings")

    provision.install_guard_hook(git_repo)

    # our merge is on disk but git ignores the change (skip-worktree)
    settings = json.loads((claude / "settings.json").read_text())
    assert HOOK_CMD in [h["command"] for h in _bash_hooks(settings)]
    status = _git(git_repo, "status", "--porcelain").stdout
    assert ".claude/settings.json" not in status
