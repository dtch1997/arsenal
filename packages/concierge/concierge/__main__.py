"""Shell shims. The Python API (concierge.Pool) is the interface; these exist
because three things must be reachable from a shell: the worker's
blocked-signal, launching the daemon, and the delegation wake probe (it runs
inside a signal_waiting `until_shell`)."""
import argparse
import asyncio

from . import delegation
from .api import Pool


def main():
    ap = argparse.ArgumentParser(prog="python -m concierge")
    ap.add_argument("--home", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("msg", help="post to a task's mailbox (workers use --from worker)")
    p.add_argument("id")
    p.add_argument("text")
    p.add_argument("--from", dest="sender", default="user", choices=["user", "worker"])

    p = sub.add_parser("serve", help="run the reconciler daemon")
    p.add_argument("--interval", type=float, default=None)
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--exit-when-idle", action="store_true", dest="exit_when_idle")

    p = sub.add_parser("probe-children",
                       help="wake probe for a delegating parent: exit 0 iff the "
                            "task's children all reached a terminal state")
    p.add_argument("id")

    args = ap.parse_args()
    overrides = {"concurrency": args.concurrency} if getattr(args, "concurrency", None) else {}
    pool = Pool(args.home, **overrides)
    if args.cmd == "msg":
        pool.msg(args.id, args.text, sender=args.sender)
        print(f"posted to {args.id} mailbox (from {args.sender})")
    elif args.cmd == "probe-children":
        raise SystemExit(delegation.probe_children(pool.home, args.id))
    else:
        asyncio.run(pool.serve(exit_when_idle=args.exit_when_idle, interval=args.interval))


main()
