"""Command-line entry point: build or serve a JSONL browser.

    databrowser serve results.jsonl --filter model,split --filter-continuous score
    databrowser build results.jsonl --out out/ --filter model
"""

from __future__ import annotations

import argparse
import sys
import time

from .core import CATEGORICAL, CONTINUOUS, FilterField, build
from .server import serve


def _filter_fields(args: argparse.Namespace):
    fields = []
    for group in args.filter or []:
        fields += [FilterField(n.strip()) for n in group.split(",") if n.strip()]
    for group in args.filter_categorical or []:
        fields += [FilterField(n.strip(), CATEGORICAL) for n in group.split(",") if n.strip()]
    for group in args.filter_continuous or []:
        fields += [FilterField(n.strip(), CONTINUOUS) for n in group.split(",") if n.strip()]
    return fields or None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="databrowser", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("data", help="path to a .jsonl or .json file")
        sp.add_argument("--filter", action="append", metavar="F1,F2",
                        help="filterable field(s); kind inferred (repeatable)")
        sp.add_argument("--filter-categorical", action="append", metavar="F1,F2",
                        help="force field(s) categorical (subset-of-values filter)")
        sp.add_argument("--filter-continuous", action="append", metavar="F1,F2",
                        help="force field(s) continuous (interval filter)")
        sp.add_argument("--title", help="browser title (default: file stem)")
        sp.add_argument("--no-strict", action="store_true",
                        help="tolerate schema mismatch (union of keys)")

    sp_serve = sub.add_parser("serve", help="build and serve over a Cloudflare tunnel")
    add_common(sp_serve)
    sp_serve.add_argument("--port", type=int, help="local port (default: random free port)")
    sp_serve.add_argument("--no-tunnel", action="store_true", help="serve locally only")

    sp_build = sub.add_parser("build", help="build the static browser into a directory")
    add_common(sp_build)
    sp_build.add_argument("--out", required=True, help="output directory")

    args = p.parse_args(argv)
    fields = _filter_fields(args)
    strict = not args.no_strict

    if args.cmd == "build":
        out = build(args.data, args.out, filter_fields=fields, title=args.title, strict=strict)
        print(f"built browser in {out}")
        print(f"open {out / 'index.html'} (serve the directory over HTTP to view)")
        return 0

    viewer = serve(
        args.data, filter_fields=fields, title=args.title, strict=strict,
        port=args.port, tunnel=not args.no_tunnel,
    )
    print(f"local:  {viewer.local_url}")
    print(f"PUBLIC: {viewer.url}")
    print("serving in the background; Ctrl-C here to stop.")
    try:
        while viewer.alive:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop()
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
