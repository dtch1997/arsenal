"""lobby CLI: status / up / serve / url / open / logs / stop / prune (+ hidden _daemon)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import webbrowser

from . import client, daemon, state


def _c(code: str, text: str) -> str:
    """ANSI-color text when stdout is a terminal."""
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


_GREEN, _RED, _DIM, _CYAN = "32", "31", "2", "36"


def _app_url(base: str, app: dict) -> str:
    return f"{base.rstrip('/')}/a/{app['name']}/"


def _cmd_status(args) -> int:
    info = client._ping(client._hub_port())
    apps = state.list_apps()
    if args.json:
        payload = {
            "hub": info,
            "apps": [
                dict(a, live=state.app_live(a),
                     url=_app_url(info["url"], a) if info else None)
                for a in apps
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0
    if info:
        bits = [f"tunnel {info.get('provider') or 'none (local)'}"]
        if info.get("started_at"):
            bits.append(f"up {state.ago(info['started_at']).removesuffix(' ago')}")
        bits.append(f"pid {info['pid']} on :{client._hub_port()}")
        print(f"hub  {_c(_CYAN, info['url'])}  {_c(_DIM, ' · '.join(bits))}")
    else:
        print("hub  not running  (start it with: lobby up)")
    if not apps:
        print(_c(_DIM, "no apps registered"))
        return 0
    apps.sort(key=lambda a: (not state.app_live(a), -(a.get("started_at") or 0)))
    name_w = max(len(a["name"]) for a in apps)
    kind_w = max(len(a.get("kind") or "app") for a in apps)
    print()
    for app in apps:
        live = state.app_live(app)
        dot = _c(_GREEN, "●") if live else _c(_RED, "○")
        kind = app.get("kind") or "app"
        when = state.ago(app.get("started_at"))
        tail = _c(_CYAN, _app_url(info["url"], app)) if (live and info) else _c(_DIM, "(ended)")
        print(f"  {dot} {app['name']:<{name_w}}  {_c(_DIM, f'{kind:<{kind_w}}')}  "
              f":{app['port']:<5} {_c(_DIM, f'{when:<10}')} {tail}")
        if app.get("title"):
            print(_c(_DIM, f"    {'':<{name_w}}  {app['title']}"))
    return 0


def _cmd_up(args) -> int:
    info = client.ensure_hub(tunnel=not args.no_tunnel, provider=args.provider)
    print(info["url"])
    return 0


def _resolve_url(name: str | None) -> str | None:
    """Public URL of the hub (no name) or a registered app; None if unavailable."""
    base = client.hub_url()
    if base is None:
        print("hub: not running (start it with: lobby up)", file=sys.stderr)
        return None
    if not name:
        return base
    slug = state.slugify(name)
    if state.read_json(state.app_path(slug)) is None:
        print(f"{name}: no app registered under that name", file=sys.stderr)
        return None
    return f"{base.rstrip('/')}/a/{slug}/"


def _cmd_url(args) -> int:
    url = _resolve_url(args.name)
    if url is None:
        return 1
    print(url)
    return 0


def _cmd_open(args) -> int:
    url = _resolve_url(args.name)
    if url is None:
        return 1
    print(url)
    webbrowser.open(url)
    return 0


def _cmd_serve(args) -> int:
    if os.path.isdir(args.target):
        url, _stop = client.serve_dir(
            args.target, name=args.name, kind=args.kind or "static",
            title=args.title, entry=args.entry, tunnel=not args.no_tunnel,
        )  # detached http.server keeps running after the CLI exits
    elif args.target.isdigit():
        url = client.serve(
            int(args.target), name=args.name, kind=args.kind or "app",
            title=args.title, entry=args.entry, tunnel=not args.no_tunnel,
            pid=0,  # the CLI exits immediately; liveness is the TCP probe
        )
    else:
        print(f"{args.target}: not a listening port or a directory", file=sys.stderr)
        return 1
    print(url)
    return 0


def _cmd_logs(args) -> int:
    path = state.state_dir() / "hub.log"
    if not path.exists():
        print(f"no hub log at {path}", file=sys.stderr)
        return 1
    lines = path.read_text(errors="replace").splitlines()
    for line in lines[-args.lines:]:
        print(line)
    if not args.follow:
        return 0
    try:
        with open(path, errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
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
    daemon.run(port=args.port, tunnel=not args.no_tunnel, provider=args.provider)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lobby", description="One tunnel for all your local apps.")
    sub = p.add_subparsers(dest="cmd", required=True)

    status = sub.add_parser("status", help="show hub URL and registered apps")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(fn=_cmd_status)

    up = sub.add_parser("up", help="start the hub (if needed) and print its URL")
    up.add_argument("--no-tunnel", action="store_true")
    up.add_argument("--provider", default=None,
                    help="tunnel backend: cloudflare (default), localhost.run, ngrok")
    up.set_defaults(fn=_cmd_up)

    serve = sub.add_parser("serve", help="register a listening port, or serve a directory")
    serve.add_argument("target", help="a port already listening on 127.0.0.1, or a directory")
    serve.add_argument("--name", default=None)
    serve.add_argument("--kind", default=None, help="default: app (port) / static (dir)")
    serve.add_argument("--title", default=None)
    serve.add_argument("--entry", default="", help="path appended to the printed URL")
    serve.add_argument("--no-tunnel", action="store_true")
    serve.set_defaults(fn=_cmd_serve)

    url = sub.add_parser("url", help="print the public URL of the hub or an app")
    url.add_argument("name", nargs="?")
    url.set_defaults(fn=_cmd_url)

    opn = sub.add_parser("open", help="open the hub index (or an app) in the browser")
    opn.add_argument("name", nargs="?")
    opn.set_defaults(fn=_cmd_open)

    logs = sub.add_parser("logs", help="show the hub daemon log")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=40)
    logs.set_defaults(fn=_cmd_logs)

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
    d.add_argument("--provider", default="cloudflare")
    d.set_defaults(fn=_cmd_daemon)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
