"""CLI entry point: read the harness JSON from stdin, print the line."""

from __future__ import annotations

import json
import sys

from statusline.render import render


def main() -> None:
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


if __name__ == "__main__":
    main()
