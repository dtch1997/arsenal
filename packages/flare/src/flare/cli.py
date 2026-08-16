"""``flare "message" [--sev info|warn|page] [--source NAME]``.

Always exits 0 — a distress channel that fails loudly (or non-zero) is worse
than useless to the background agent that just tried to page for help.
"""

from __future__ import annotations

import argparse
import sys

from .core import SEVERITIES, send


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="flare", description="page Daniel over Slack")
    p.add_argument("message", help="the distress message")
    p.add_argument("--sev", choices=SEVERITIES, default="info",
                   help="severity (default: info)")
    p.add_argument("--source", default=None,
                   help="short label for the emitter (e.g. concierge, pod-42)")
    args = p.parse_args(argv)

    try:
        record = send(args.message, sev=args.sev, source=args.source)
    except Exception as e:  # belt-and-suspenders: never non-zero on our account
        print(f"flare: {e}", file=sys.stderr)
        return 0

    if record["sent"]:
        print(f"[{record['sev']}] sent")
    elif record["suppressed"]:
        print(f"[{record['sev']}] suppressed (duplicate within 10m); spooled")
    # the no-webhook line is printed by send() itself
    return 0


if __name__ == "__main__":
    sys.exit(main())
