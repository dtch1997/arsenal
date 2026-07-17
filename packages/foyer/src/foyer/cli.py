"""CLI: `foyer serve` (own tunnel via lobby.tunnel), `foyer url`, `foyer token`.

foyer deliberately does NOT register with the lobby hub: the hub's reverse
proxy buffers whole responses and can't pass websockets, and the hub index is
public-by-design while foyer is a shell. It opens its own quick tunnel with
the shared `lobby.tunnel` provider seam instead.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from aiohttp import web

from .server import FOYER_HOME, build_app, load_token

STATE = FOYER_HOME / "state.json"


def _tokened(base: str, token: str) -> str:
    return f"{base.rstrip('/')}/?t={token}"


def serve(port: int, host: str, use_tunnel: bool) -> None:
    from . import relay

    token = load_token()
    app = build_app(token)
    public = f"http://{host}:{port}"
    stop = None
    if use_tunnel:
        from lobby.tunnel import tunnel
        public, stop = tunnel(port)
    stable = None
    if use_tunnel and relay.config():
        try:
            stable = relay.publish(public)
        except relay.RelayError as e:
            print(f"foyer: relay publish failed ({e}); "
                  "falling back to the tunnel URL", flush=True)
    STATE.write_text(json.dumps(
        {"url": public, "stable_url": stable, "port": port,
         "pid": os.getpid(), "started_at": time.time()}
    ))
    print(f"foyer: serving on http://{host}:{port}", flush=True)
    if stable:
        print(f"foyer: STABLE url  {_tokened(stable, token)}", flush=True)
        print(f"foyer: (tunnel this restart: {public})", flush=True)
    else:
        print(f"foyer: open {_tokened(public, token)}", flush=True)
    print("foyer: the URL+token is shell access — treat it like a password; "
          f"rotate by deleting {FOYER_HOME / 'token'}", flush=True)
    try:
        web.run_app(app, host=host, port=port, print=None)
    finally:
        if stop is not None:
            stop()
        STATE.unlink(missing_ok=True)


def url() -> None:
    if not STATE.exists():
        raise SystemExit("foyer: not serving (no state file)")
    state = json.loads(STATE.read_text())
    print(_tokened(state.get("stable_url") or state["url"], load_token()))


def relay_cmd(action: str) -> None:
    from . import relay

    if action == "up":
        base = relay.up()
        print(f"foyer: relay ready at {base}")
        print("foyer: restart `foyer serve` to publish the tunnel to it")
    elif action == "status":
        cfg = relay.config()
        if not cfg:
            raise SystemExit("foyer: no relay configured (run `foyer relay up`)")
        base = relay.stable_url(cfg["pod_id"])
        info = relay.ping(base)
        if info is None:
            print(f"foyer: relay {base} NOT answering (pod {cfg['pod_id']})")
        else:
            tgt = "target published" if info.get("target_set") else "NO target yet"
            print(f"foyer: relay {base} up — {tgt}")
    elif action == "delete":
        cfg = relay.config()
        if not cfg:
            raise SystemExit("foyer: no relay configured")
        relay.delete_pod(cfg["pod_id"])
        relay.CONFIG.unlink(missing_ok=True)
        print(f"foyer: relay pod {cfg['pod_id']} deleted")


def main() -> None:
    p = argparse.ArgumentParser(prog="foyer", description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    ps = sub.add_parser("serve", help="serve the foyer UI")
    ps.add_argument("--port", type=int, default=4711)
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--no-tunnel", action="store_true",
                    help="localhost only (port-forward yourself)")
    sub.add_parser("url", help="print the current tokened URL")
    sub.add_parser("token", help="print the auth token")
    pr = sub.add_parser("relay", help="manage the stable-URL relay pod")
    pr.add_argument("action", choices=["up", "status", "delete"])
    args = p.parse_args()
    if args.cmd == "serve":
        serve(args.port, args.host, use_tunnel=not args.no_tunnel)
    elif args.cmd == "url":
        url()
    elif args.cmd == "relay":
        relay_cmd(args.action)
    elif args.cmd == "token":
        print(load_token())
    else:
        p.print_help()


if __name__ == "__main__":
    main()
