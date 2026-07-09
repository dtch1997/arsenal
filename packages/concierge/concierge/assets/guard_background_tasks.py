#!/usr/bin/env python3
"""
PreToolUse(Bash) guard: block background-task anti-patterns that silently drop
the harness completion notification.

The harness fires a <task-notification> ONLY when the process it tracks actually
exits. Detaching the real work (nohup/disown/setsid, `cmd & echo ...`) makes it
track the launcher instead, and a self-matching `pgrep -f` watcher loop never
exits because it sees its own command line. Both look like "still running"
forever. See ~/.claude/CLAUDE.md "Background tasks".

Contract: read hook JSON on stdin. exit 0 = allow. exit 2 + stderr = block and
show the message to Claude. Any internal error -> exit 0 (fail open; never wedge
the session over a guard bug).
"""
import sys
import json
import re

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

ti = data.get("tool_input") or {}
cmd = ti.get("command") or ""
bg = bool(ti.get("run_in_background", False))


def deny(msg: str) -> None:
    sys.stderr.write(msg.strip() + "\n")
    sys.exit(2)


# --- Anti-pattern 1: self-matching pgrep watcher loop (any task) ---
has_loop = re.search(r"\b(while|until)\b", cmd) is not None
has_pgrep_f = re.search(r"\bpgrep\b[^\n;|&]*?-[A-Za-z]*f", cmd) is not None
if has_loop and has_pgrep_f:
    deny(
        "Blocked: `while/until` loop polling `pgrep -f` is a self-matching watcher.\n"
        "`pgrep -f` matches the FULL command line, so the loop matches its OWN cmdline "
        "and never exits -> no completion notification, stuck shells pile up.\n"
        "Instead:\n"
        "  - Run the real command directly with run_in_background:true and just wait "
        "for the <task-notification> (no watcher needed).\n"
        "  - If you truly need a wait-loop, gate on a log marker: "
        "`until grep -q \"completed successfully\" run.log; do sleep 30; done`,\n"
        "    or match a pidfile / a string that can't appear in the watcher's own cmdline."
    )

# --- Anti-pattern 2: detaching the real work inside a background task ---
if bg:
    if re.search(r"\bnohup\b", cmd):
        deny(
            "Blocked: `nohup` inside a run_in_background command detaches the real work.\n"
            "The harness then notifies on the launcher's immediate exit, not the job -> "
            "the job is orphaned and never signals completion.\n"
            "Fix: drop `nohup` and run the command directly with run_in_background:true."
        )
    if re.search(r"\b(disown|setsid)\b", cmd):
        deny(
            "Blocked: `disown`/`setsid` detaches the real work from the tracked process.\n"
            "The completion <task-notification> only fires when the TRACKED process exits.\n"
            "Fix: drop the detach and run the command directly with run_in_background:true."
        )
    # A single `&` used to background then continue/exit (excludes && and &>/&>>).
    if re.search(r"(?<!&)&(?!&)(?!>)\s*(echo\b|disown\b|exit\b|;|#|\n|\Z)", cmd):
        deny(
            "Blocked: backgrounding with `&` inside a run_in_background command.\n"
            "`cmd & echo launched` makes the harness track the launcher (exits instantly), "
            "not `cmd` -> no completion notification for the real work.\n"
            "Fix: pass the real long command as the run_in_background command itself, "
            "with no trailing `&`."
        )

sys.exit(0)
