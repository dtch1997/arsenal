"""cowrite command-line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__, manager, server


def _cmd_serve(args: argparse.Namespace) -> None:
    st = manager.serve(args.path, slug=args.slug, title=args.title,
                       port=args.port, no_tunnel=args.no_tunnel)
    print(f"slug:      {st['slug']}")
    print(f"editing:   {st['source']}")
    print(f"local:     http://127.0.0.1:{st['port']}/")
    print(f"PUBLIC:    {st['full_url']}")
    print(f"teardown:  cowrite stop {st['slug']}")


def _cmd_server(args: argparse.Namespace) -> None:
    server.run_server(args.file, args.port, args.title)


def _cmd_list(_args: argparse.Namespace) -> None:
    states = manager.list_editors()
    if not states:
        print("no editors recorded.")
        return
    for st in states:
        flag = "LIVE" if manager._is_alive(st) else "dead"
        print(f"[{flag}] {st['slug']:<24} {st.get('full_url', '?')}")
        print(f"        editing: {st.get('source', '?')}  started: {st.get('started_at', '?')}")


def _cmd_stop(args: argparse.Namespace) -> None:
    for s in manager.stop(slug=args.slug, all_=args.all):
        print(f"stopped {s}")


def _cmd_prune(_args: argparse.Namespace) -> None:
    pruned = manager.prune()
    if not pruned:
        print("nothing to prune (no dead editors).")
        return
    for s in pruned:
        print(f"pruned {s}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="cowrite",
        description="Co-write a Markdown draft in the browser: side-by-side editor with "
                    "save-to-disk round-trip, served over a Cloudflare quick tunnel.",
    )
    ap.add_argument("--version", action="version", version=f"cowrite {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="start an editor for a Markdown draft")
    sp.add_argument("path", help="Markdown draft file (.md/.markdown); created empty if missing")
    sp.add_argument("--slug", help="name for this editor (default: from filename)")
    sp.add_argument("--title", help="title shown in the editor header")
    sp.add_argument("--port", type=int, help="local port (default: random free port)")
    sp.add_argument("--no-tunnel", action="store_true",
                    help="serve on localhost only; skip the Cloudflare tunnel")
    sp.set_defaults(func=_cmd_serve)

    lp = sub.add_parser("list", help="list active editors")
    lp.set_defaults(func=_cmd_list)

    stp = sub.add_parser("stop", help="tear down an editor")
    stp.add_argument("slug", nargs="?", help="editor slug")
    stp.add_argument("--all", action="store_true", help="stop every editor")
    stp.set_defaults(func=_cmd_stop)

    pp = sub.add_parser("prune", help="tear down only dead/stale editors, keep live ones")
    pp.set_defaults(func=_cmd_prune)

    # internal: the foreground HTTP server, launched detached by `serve`
    svp = sub.add_parser("_server")
    svp.add_argument("--file", required=True)
    svp.add_argument("--port", type=int, required=True)
    svp.add_argument("--title")
    svp.set_defaults(func=_cmd_server)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
