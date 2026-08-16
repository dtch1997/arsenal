"""``desk render|digest|sync|serve`` — the waiting-on-Daniel inbox."""

from __future__ import annotations

import sys
import time

from .core import digest, render, sync


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="desk", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render", help="write ~/.desk/inbox.md and print its path")
    sub.add_parser("digest", help="print a short plaintext summary to stdout")
    sub.add_parser("sync", help="render, flare newly-appearing items, update state")
    sp_serve = sub.add_parser("serve", help="serve the inbox through the lobby hub")
    sp_serve.add_argument("--port", type=int, help="local port (default: free port)")
    sp_serve.add_argument("--interval", type=int, default=60,
                          help="re-render interval in seconds (default: 60)")
    sp_serve.add_argument("--no-tunnel", action="store_true",
                          help="serve locally only, skip the lobby hub")
    args = p.parse_args(argv)

    if args.cmd == "render":
        print(render())
        return 0

    if args.cmd == "digest":
        print(digest())
        return 0

    if args.cmd == "sync":
        result = sync()
        print(f"{result['path']}: {result['new']} new item(s), "
              f"{result['flared']} flare(s) sent")
        return 0

    if args.cmd == "serve":
        from .server import serve
        srv = serve(interval=args.interval, port=args.port,
                    tunnel=not args.no_tunnel)
        print(f"local:  {srv.local_url}")
        if srv.url != srv.local_url:
            print(f"PUBLIC: {srv.url}")
        print("serving in the background; Ctrl-C here to stop.")
        try:
            while srv.alive:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            srv.stop()
            print("\nstopped.")
        return 0

    return 1  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
