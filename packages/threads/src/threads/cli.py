"""``threads scan|weave|serve|render|status`` — the bottom-up activity spine.

``scan``/``weave`` take ``--check`` gate hooks that are cheap and offline (no
model calls): they exit 0 iff the spool is complete and consistent.
"""

from __future__ import annotations

import sys
import time

from . import config


def _cmd_scan(args) -> int:
    from .scan import scan, scan_check
    if args.check:
        ok, report = scan_check(days=args.days)
        print(report)
        return 0 if ok else 1
    res = scan(days=args.days, max_calls=args.max_calls, all_=args.all)
    print(res.report())
    for e in res.errors[:20]:
        print(f"  ! {e}", file=sys.stderr)
    return 0


def _cmd_weave(args) -> int:
    from .weave import weave, weave_check
    if args.check:
        ok, report = weave_check()
        print(report)
        return 0 if ok else 1
    res = weave(cluster=not args.no_cluster)
    print(res.report())
    return 0


def _cmd_render(args) -> int:
    from .dashboard import render_markdown
    print(render_markdown())
    return 0


def _cmd_status(args) -> int:
    from .dashboard import build
    from .scan import scan_check
    from .weave import weave_check
    dash = build()
    dormant = [t.slug for t in dash.threads if t.dormant]
    print(f"threads: {len(dash.threads)} active, {len(dash.unfiled)} unfiled, "
          f"{len(dash.candidates)} candidate(s); match rate "
          f"{dash.match_rate * 100:.0f}%")
    if dormant:
        print(f"  dormant (>{dash.dormant_days}d): {', '.join(dormant)}")
    s_ok, _ = scan_check(days=config.DEFAULT_LOOKBACK_DAYS)
    w_ok, _ = weave_check()
    print(f"  scan --check: {'OK' if s_ok else 'FAIL'} · "
          f"weave --check: {'OK' if w_ok else 'FAIL'}")
    return 0


def _cmd_serve(args) -> int:
    from .server import serve
    srv = serve(interval=args.interval, port=args.port, tunnel=not args.no_tunnel)
    # flush=True so the URL lands in the log immediately under `tmux ... > log`
    # (the process then blocks in the keep-alive loop and never flushes on exit).
    print(f"local:  {srv.local_url}", flush=True)
    if srv.url != srv.local_url:
        print(f"PUBLIC: {srv.url}", flush=True)
    print("serving in the background; Ctrl-C here to stop.", flush=True)
    try:
        while srv.alive:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        print("\nstopped.")
    return 0


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="threads", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="summarize new/grown sessions")
    sp.add_argument("--days", type=int, default=config.DEFAULT_LOOKBACK_DAYS,
                    help=f"lookback window (default: {config.DEFAULT_LOOKBACK_DAYS})")
    sp.add_argument("--max-calls", type=int, default=config.DEFAULT_MAX_CALLS,
                    help=f"per-run model-call cap (default: {config.DEFAULT_MAX_CALLS})")
    sp.add_argument("--all", action="store_true",
                    help="lift the call cap (backfill)")
    sp.add_argument("--check", action="store_true",
                    help="offline gate: spool covers the window and is a no-op re-scan")

    wp = sub.add_parser("weave", help="assign summaries to threads")
    wp.add_argument("--no-cluster", action="store_true",
                    help="skip the candidate-thread clustering model call")
    wp.add_argument("--check", action="store_true",
                    help="offline gate: coverage + deterministic-match-rate thresholds")

    sub.add_parser("render", help="print the dashboard as a markdown digest")
    sub.add_parser("status", help="one-line activity + gate summary")

    sv = sub.add_parser("serve", help="serve the dashboard through the lobby hub")
    sv.add_argument("--port", type=int, help="local port (default: free port)")
    sv.add_argument("--interval", type=int, default=60,
                    help="re-render interval in seconds (default: 60)")
    sv.add_argument("--no-tunnel", action="store_true",
                    help="serve locally only, skip the lobby hub")

    args = p.parse_args(argv)
    return {
        "scan": _cmd_scan, "weave": _cmd_weave, "render": _cmd_render,
        "status": _cmd_status, "serve": _cmd_serve,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
