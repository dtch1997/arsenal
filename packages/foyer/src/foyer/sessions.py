"""tmux session discovery — the sidebar's data source.

One row per tmux session, described by its *active* pane: where it is
(`pane_current_path`), what runs in it (`pane_current_command`), what the
program says about itself (`pane_title` — Claude Code writes its live status
there), and a short tail of the screen as a preview.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

_SEP = "\x1f"  # unit separator: never appears in tmux format output naturally


def tmux_cmd() -> list[str]:
    """The tmux invocation to use; override with FOYER_TMUX (e.g. a -L test socket)."""
    return shlex.split(os.environ.get("FOYER_TMUX", "tmux"))

_FIELDS = [
    "#{session_name}",
    "#{session_created}",
    "#{session_attached}",
    "#{session_activity}",
    "#{window_active}",
    "#{pane_active}",
    "#{pane_current_path}",
    "#{pane_current_command}",
    "#{pane_title}",
]

# Commands that mean "an agent is running in this pane right now".
_AGENT_COMMANDS = {"claude", "node"}


def _tmux(*args: str) -> str:
    out = subprocess.run(
        [*tmux_cmd(), *args], capture_output=True, text=True, timeout=10
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"tmux {' '.join(args)} failed")
    return out.stdout


def exists(name: str) -> bool:
    try:
        _tmux("has-session", "-t", f"={name}")
        return True
    except RuntimeError:
        return False


def names() -> set[str]:
    try:
        return set(_tmux("list-sessions", "-F", "#{session_name}").splitlines())
    except RuntimeError:
        return set()


def create(name: str, cwd: str, command: str | None = None) -> None:
    """New detached session at `cwd`; optionally type `command` into it.

    The command goes in via send-keys (rather than as the session command) so
    the shell — and the thread — survive the program exiting.
    """
    _tmux("new-session", "-d", "-s", name, "-c", cwd)
    if command:
        _tmux("send-keys", "-t", f"={name}:", command, "Enter")


def rename(old: str, new: str) -> None:
    _tmux("rename-session", "-t", f"={old}", new)


def preview(name: str, lines: int = 6) -> list[str]:
    """Last few non-empty screen lines of the session's active pane.

    Pane-targeting commands need the trailing colon (`=name:` = the session's
    current window): on tmux 3.2a a bare `=name` fails with "can't find pane".
    """
    try:
        raw = _tmux("capture-pane", "-p", "-t", f"={name}:", "-S", f"-{lines * 4}")
    except RuntimeError:
        return []
    tail = [ln.rstrip() for ln in raw.splitlines() if ln.strip()]
    return tail[-lines:]


def list_sessions(with_preview: bool = True) -> list[dict]:
    try:
        raw = _tmux("list-panes", "-a", "-F", _SEP.join(_FIELDS))
    except RuntimeError:
        return []  # no tmux server running yet
    out: dict[str, dict] = {}
    for line in raw.splitlines():
        parts = line.split(_SEP)
        if len(parts) != len(_FIELDS):
            continue
        (name, created, attached, activity,
         win_active, pane_active, cwd, command, title) = parts
        if name in out and not (win_active == "1" and pane_active == "1"):
            continue  # keep the active pane's row for each session
        out[name] = {
            "name": name,
            "created": int(created or 0),
            "attached": int(attached or 0) > 0,
            "activity": int(activity or 0),
            "cwd": cwd,
            "dir": Path(cwd).name if cwd else "",
            "command": command,
            "title": title,
            "agent": command in _AGENT_COMMANDS,
        }
    rows = sorted(out.values(), key=lambda r: -r["activity"])
    if with_preview:
        for row in rows:
            row["preview"] = preview(row["name"], lines=4)
    return rows
