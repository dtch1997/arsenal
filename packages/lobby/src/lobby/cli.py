"""lobby CLI: status / up / stop / prune (+ hidden _daemon)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from . import client, daemon, state


def _cmd_status(args) -> int:
    info = client._ping(client._hub_port())
    if info:
        print(f"hub: {info['url']}  (pid {info['pid']}, local port {client._hub_port()})")
    else:
        print("hub: not running")
    apps = state.list_apps()
    if not apps:
        print("apps: none registered")
        return 0
    for app in apps:
        live = "live " if state.app_live(app) else "ended"
        title = f"  {app['title']}" if app.get("title") else ""
        print(
            f"  [{live}] {app['name']:<24} {app.get('kind', 'app'):<12} "
            f"port {app['port']:<6} started {state.ago(app.get('started_at'))}{title}"
        )
    return 0


def _cmd_up(args) -> int:
    info = client.ensure_hub(tunnel=not args.no_tunnel)
    print(info["url"])
    return 0


def _cmd_stop(args) -> int:
    if args.hub:
        hub = state.read_json(state.hub_path())
        if hub and state.pid_alive(hub.get("pid")):
            os.kill(hub["pid"], signal.SIGTERM)
            print(f"stopped hub (pid {hub['pid']})")
        else:
            print("hub: not running")
        return 0
    names = args.name or []
    if args.all:
        names = [a["name"] for a in state.list_apps()]
    if not names:
        print("nothing to stop (pass app names, --all, or --hub)", file=sys.stderr)
        return 1
    for name in names:
        app = state.read_json(state.app_path(name))
        if app is None:
            print(f"{name}: unknown")
            continue
        if app.get("pid") and state.pid_alive(app["pid"]):
            os.kill(app["pid"], signal.SIGTERM)
        state.app_path(name).unlink(missing_ok=True)
        print(f"stopped {name}")
    return 0


def _cmd_prune(args) -> int:
    removed = []
    for app in state.list_apps():
        if not state.app_live(app):
            state.app_path(app["name"]).unlink(missing_ok=True)
            removed.append(app["name"])
    print(f"pruned {len(removed)}: {', '.join(removed) or '-'}")
    return 0


def _cmd_daemon(args) -> int:
    daemon.run(port=args.port, tunnel=not args.no_tunnel)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lobby", description="One tunnel for all your local apps.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show hub URL and registered apps").set_defaults(fn=_cmd_status)

    up = sub.add_parser("up", help="start the hub (if needed) and print its URL")
    up.add_argument("--no-tunnel", action="store_true")
    up.set_defaults(fn=_cmd_up)

    stop = sub.add_parser("stop", help="stop apps by name, or the hub itself")
    stop.add_argument("name", nargs="*")
    stop.add_argument("--all", action="store_true")
    stop.add_argument("--hub", action="store_true")
    stop.set_defaults(fn=_cmd_stop)

    sub.add_parser("prune", help="drop state for apps that are no longer running").set_defaults(
        fn=_cmd_prune
    )

    d = sub.add_parser("_daemon")  # internal: foreground hub, spawned by ensure_hub()
    d.add_argument("--port", type=int, default=state.DEFAULT_PORT)
    d.add_argument("--no-tunnel", action="store_true")
    d.set_defaults(fn=_cmd_daemon)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
