"""LLM news pass — turn the last 24h of merges into a few narrative bullets.

A flat merge list is a git log; the consumer wants release notes. This shells
out to a headless ``claude -p`` (configurable via ``synthesis_cmd``; empty
string disables) and degrades to ``None`` on any failure — missing binary,
timeout, empty or implausible output — so the notes fall back to the flat
list rather than ever blocking the morning cron.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Config

# below this many merges a flat list reads fine on its own
MIN_MERGES = 3
TIMEOUT_S = 180
MAX_OUTPUT_CHARS = 2500

PROMPT = """\
You write the News section of morning patch notes for Daniel, who consumes an
autonomous agent system: these PRs merged overnight without his involvement.
Group them into 3-6 markdown bullets by theme/work-thread. Lead each bullet
with the capability change from the reader's perspective ("you can now ..."
where it fits), and end it with the PR refs in parentheses, e.g. (jarvis#136,
arsenal#48). Order bullets user-visible-capabilities first, chores last.
Output only the bullets — no preamble, no headers, no code fences.

Merged PRs:
{merges}
"""


def news_prompt(merged: list[dict]) -> str:
    rows = []
    for m in merged:
        repo_short = m["repo"].split("/")[-1]
        rows.append(f"- {repo_short}#{m['number']}: {m['title']}")
    return PROMPT.format(merges="\n".join(rows))


def _resolve(cmd: list[str]) -> list[str] | None:
    exe = shutil.which(cmd[0])
    if not exe:
        # cron PATH is minimal; agent CLIs live in ~/.local/bin (ops/link-clis.sh)
        candidate = Path.home() / ".local" / "bin" / cmd[0]
        exe = str(candidate) if candidate.exists() else None
    return [exe, *cmd[1:]] if exe else None


def _plausible(text: str) -> bool:
    return bool(text) and len(text) <= MAX_OUTPUT_CHARS and text.lstrip().startswith(("-", "*", "•"))


def synthesize(cfg: Config, merged: list[dict]) -> str | None:
    if not cfg.synthesis_cmd or len(merged) < MIN_MERGES:
        return None
    cmd = _resolve(shlex.split(cfg.synthesis_cmd))
    if not cmd:
        return None
    try:
        proc = subprocess.run(
            cmd, input=news_prompt(merged), capture_output=True, text=True, timeout=TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text if _plausible(text) else None
