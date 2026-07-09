"""Workspace provisioning helpers — make a freshly-created workspace safe.

Concierge workspaces are bare clones (or bare `mkdir`s); unlike the jarvis
checkout they carry no Claude Code hooks. Workers have repeatedly launched GPU
pipelines as `nohup … &` inside `run_in_background` Bash calls, orphaning the
real job. `install_guard_hook` drops the same PreToolUse(Bash) guard the jarvis
repo uses into every workspace and registers it in the workspace's Claude
settings, merging (never clobbering) a cloned repo's own settings and keeping
the additions out of worker PRs.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
GUARD_ASSET = ASSETS / "guard_background_tasks.py"

# Relative to the workspace root. The hook command references the copied script
# via $CLAUDE_PROJECT_DIR (set by Claude Code to the project/workspace root when
# it runs hooks), so it stays valid regardless of the absolute workspace path.
_HOOK_REL = ".claude/hooks/guard_background_tasks.py"
_SETTINGS_REL = ".claude/settings.json"
_HOOK_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/guard_background_tasks.py"'
_HOOK_ENTRY = {
    "type": "command",
    "command": _HOOK_COMMAND,
    "timeout": 10,
    "statusMessage": "Checking background-task safety",
}


def install_guard_hook(ws: Path) -> None:
    """Copy the background-task guard into the workspace and register it as a
    PreToolUse(Bash) hook. Idempotent; safe on both the clone and mkdir paths."""
    ws = Path(ws)
    hook_dst = ws / _HOOK_REL
    hook_dst.parent.mkdir(parents=True, exist_ok=True)
    hook_dst.write_text(GUARD_ASSET.read_text())
    hook_dst.chmod(0o755)

    settings_path = ws / _SETTINGS_REL
    settings, settings_tracked = {}, False
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
        settings_tracked = _git_tracked(ws, _SETTINGS_REL)
    if not isinstance(settings, dict):
        settings = {}
    _merge_bash_hook(settings)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    # Keep our additions out of worker PRs: exclude untracked files, and
    # skip-worktree a settings.json the target repo already tracks.
    if (ws / ".git").exists():
        _git_exclude(ws, [_HOOK_REL] + ([] if settings_tracked else [_SETTINGS_REL]))
        if settings_tracked:
            subprocess.run(["git", "-C", str(ws), "update-index", "--skip-worktree", _SETTINGS_REL],
                           check=False, capture_output=True)


def _merge_bash_hook(settings: dict) -> None:
    """Add the guard to settings['hooks']['PreToolUse'] under the Bash matcher,
    preserving any hooks the target repo already declares. Idempotent."""
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = settings["hooks"] = {}
    pre = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre, list):
        pre = hooks["PreToolUse"] = []
    for group in pre:
        if isinstance(group, dict) and group.get("matcher") == "Bash":
            group_hooks = group.setdefault("hooks", [])
            if not isinstance(group_hooks, list):
                group_hooks = group["hooks"] = []
            if any(isinstance(h, dict) and h.get("command") == _HOOK_COMMAND for h in group_hooks):
                return
            group_hooks.append(dict(_HOOK_ENTRY))
            return
    pre.append({"matcher": "Bash", "hooks": [dict(_HOOK_ENTRY)]})


def _git_tracked(ws: Path, rel: str) -> bool:
    r = subprocess.run(["git", "-C", str(ws), "ls-files", "--error-unmatch", rel],
                       capture_output=True, text=True)
    return r.returncode == 0


def _git_exclude(ws: Path, rels: list[str]) -> None:
    exclude = ws / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    existing = exclude.read_text().splitlines() if exclude.exists() else []
    have = set(existing)
    add = [r for r in rels if r not in have]
    if not add:
        return
    lines = existing + ["# concierge: workspace guard hook (do not commit)"] + add
    exclude.write_text("\n".join(lines) + "\n")
