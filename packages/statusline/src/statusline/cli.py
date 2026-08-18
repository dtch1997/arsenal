"""CLI: with no arguments, render mode (harness JSON on stdin → line on
stdout — this is what ~/.claude/statusline.sh execs). Subcommands write the
per-session sidecar the renderer picks up:

    claude-statusline note "repro logit-interp headline"   # set topic
    claude-statusline note --clear                         # back to auto session_name
    claude-statusline flag "open PR" "push branch"         # add wrap-up flags
    claude-statusline unflag "open PR"                     # remove matching flags
    claude-statusline unflag --all
    claude-statusline show                                 # print sidecar state

Writers key on $CLAUDE_CODE_SESSION_ID (set in the session's Bash env) or
--session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from statusline import session
from statusline.render import render


def _render_mode() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    # The status line must never crash the harness's refresh loop: any
    # rendering error degrades to a bare fallback instead of a traceback.
    try:
        line = render(payload)
    except Exception:
        line = "[Claude] Context: --%"
    print(line, end="")


def _session_id(args: argparse.Namespace) -> str:
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        sys.exit("no session id: pass --session or run inside a Claude Code session")
    return sid


def main() -> None:
    if len(sys.argv) == 1:
        _render_mode()
        return

    parser = argparse.ArgumentParser(prog="claude-statusline", description=__doc__)
    parser.add_argument("--session", help="session id (default: $CLAUDE_CODE_SESSION_ID)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("note", help="set the session topic shown in the status line")
    p.add_argument("topic", nargs="?", help="topic text")
    p.add_argument("--clear", action="store_true", help="revert to the auto session name")

    p = sub.add_parser("flag", help="add wrap-up flag(s): things to do before ending the session")
    p.add_argument("text", nargs="+", help="one or more flag strings")

    p = sub.add_parser("unflag", help="remove flags matching TEXT (case-insensitive substring)")
    p.add_argument("text", nargs="?", help="substring to match")
    p.add_argument("--all", action="store_true", help="remove every flag")

    sub.add_parser("show", help="print this session's sidecar state")

    args = parser.parse_args()
    sid = _session_id(args)
    state = session.load(sid)

    if args.cmd == "note":
        if args.clear:
            state.pop("topic", None)
        elif args.topic:
            state["topic"] = args.topic
        else:
            parser.error("note requires a topic or --clear")
        session.save(sid, state)
    elif args.cmd == "flag":
        flags = state.setdefault("flags", [])
        flags.extend(t for t in args.text if t not in flags)
        session.save(sid, state)
    elif args.cmd == "unflag":
        if args.all:
            state["flags"] = []
        elif args.text:
            needle = args.text.lower()
            state["flags"] = [f for f in state.get("flags", []) if needle not in f.lower()]
        else:
            parser.error("unflag requires TEXT or --all")
        session.save(sid, state)
    elif args.cmd == "show":
        print(json.dumps(state, indent=2))
        return

    flags = state.get("flags", [])
    print(f"topic: {state.get('topic') or '(auto)'} | flags: {'; '.join(flags) or '(none)'}")


if __name__ == "__main__":
    main()
