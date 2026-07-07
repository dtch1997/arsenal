"""Completion gates — evaluated by the pool after worker exit, never self-reported.

Semantics borrowed from flightdeck's exit criteria.
"""
from __future__ import annotations

import json
import subprocess


def check(task, workspace) -> tuple[bool, str, dict]:
    """Return (passed, detail, links) — links get stamped onto the task on pass."""
    gate = task["gate"]
    kind, arg = gate["kind"], gate.get("arg")
    if kind == "always":
        return True, "always", {}
    if kind == "file_exists":
        present = (workspace / arg).exists()
        return present, f"file_exists({arg}): {'present' if present else 'missing'}", {}
    if kind == "shell_ok":
        try:
            r = subprocess.run(arg, shell=True, cwd=workspace,
                               capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return False, f"shell_ok: timed out after 600s: {arg}", {}
        tail = (r.stdout + r.stderr).strip()[-500:]
        return r.returncode == 0, f"shell_ok rc={r.returncode}: {tail or arg}", {}
    if kind in ("pr_open", "pr_merged"):
        want = "OPEN" if kind == "pr_open" else "MERGED"
        branch = arg or task["workspace"]["branch"]
        r = subprocess.run(["gh", "pr", "view", branch, "--json", "state,url"],
                           cwd=workspace, capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"{kind}({branch}): no PR ({r.stderr.strip()[:200]})", {}
        info = json.loads(r.stdout)
        return (info["state"] == want,
                f"{kind}({branch}): state={info['state']} {info['url']}",
                {"pr": info["url"]})
    return False, f"unknown gate kind {kind!r}", {}
