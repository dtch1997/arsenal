"""Thin CLI mirroring the Python API: ``ferry push|pull|remotes``."""

from __future__ import annotations

import argparse
import sys

from ferry.core import RcloneError, RcloneNotFound, listremotes, pull, push


def _add_transfer_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mirror", action="store_true", help="rclone sync (deletes extras on dest) instead of additive copy")
    p.add_argument("--dry-run", action="store_true", help="show what would change, transfer nothing")
    p.add_argument("--exclude", action="append", default=[], metavar="PATTERN", help="exclude pattern (repeatable)")
    p.add_argument("--include", action="append", default=[], metavar="PATTERN", help="include pattern (repeatable)")
    p.add_argument("--transfers", type=int, default=None, help="parallel file transfers")
    p.add_argument("--checkers", type=int, default=None, help="parallel checkers")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ferry", description="Pythonic push/pull over rclone.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="local -> remote")
    p_push.add_argument("local")
    p_push.add_argument("remote")
    _add_transfer_flags(p_push)

    p_pull = sub.add_parser("pull", help="remote -> local")
    p_pull.add_argument("remote")
    p_pull.add_argument("local")
    _add_transfer_flags(p_pull)

    sub.add_parser("remotes", help="list configured rclone remotes")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "remotes":
            for name in listremotes():
                print(name)
            return 0

        common = dict(
            mirror=args.mirror,
            dry_run=args.dry_run,
            excludes=args.exclude,
            includes=args.include,
            transfers=args.transfers,
            checkers=args.checkers,
        )
        if args.cmd == "push":
            push(args.local, args.remote, **common)
        elif args.cmd == "pull":
            pull(args.remote, args.local, **common)
        return 0
    except RcloneNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 127
    except RcloneError as e:
        print(f"rclone failed (exit {e.returncode})", file=sys.stderr)
        return e.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
