"""Command-line entry point.

    reportly new <slug>            # scaffold a report from the template
    reportly lint [path]           # enforce the standard (file or reports dir)
    reportly build [path] [--out]  # render md -> html + index
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from . import lint as lint_mod
from .build import build as build_site
from .scaffold import scaffold as scaffold_report


def _load_config(path_hint: str) -> config_mod.Config:
    return config_mod.load(path_hint or ".")


def _cmd_new(args) -> int:
    cfg = _load_config(args.reports_dir)
    reports_dir = args.reports_dir or cfg.reports_dir
    out = scaffold_report(args.slug, reports_dir, title=args.title,
                          branch=args.branch, force=args.force)
    print(f"created {out}")
    print(f"edit it, drop the headline figure in {Path(reports_dir) / 'figs'}/, "
          f"then `reportly lint {reports_dir}`")
    return 0


def _cmd_lint(args) -> int:
    path = args.path or _load_config(".").reports_dir
    cfg = _load_config(path)
    if args.level:
        cfg.level = args.level
    results = lint_mod.lint_path(path, cfg)
    if not results:
        print(f"no reports found at {path}", file=sys.stderr)
        return 1

    n_err = n_warn = n_fail = 0
    for fpath, issues in results.items():
        printed = [i for i in issues if cfg.fail_on_warn or i.severity == lint_mod.ERROR
                   or args.show_warnings]
        for i in printed:
            print(i.format())
            n_err += i.severity == lint_mod.ERROR
            n_warn += i.severity == lint_mod.WARN
        if lint_mod.is_failure(issues, cfg):
            n_fail += 1

    ok = len(results) - n_fail
    print(f"\n{ok}/{len(results)} report(s) pass · {n_err} error(s), {n_warn} warning(s)",
          file=sys.stderr)
    return 1 if n_fail else 0


def _cmd_build(args) -> int:
    path = args.path or _load_config(".").reports_dir
    cfg = _load_config(path)
    out = build_site(path, args.out, cfg)
    n = len(list(Path(out).glob("*.html"))) - 1  # minus index.html
    print(f"rendered {max(n, 0)} report(s) -> {Path(out) / 'index.html'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="reportly", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("new", help="scaffold a report from the template")
    sp.add_argument("slug", help="report slug (filename + figure prefix)")
    sp.add_argument("--reports-dir", default="", help="reports directory (default: from config)")
    sp.add_argument("--title", help="H1 thesis (default: a placeholder to fill in)")
    sp.add_argument("--branch", help="branch name for the provenance footer")
    sp.add_argument("--force", action="store_true", help="overwrite if it exists")
    sp.set_defaults(func=_cmd_new)

    sp = sub.add_parser("lint", help="enforce the standard")
    sp.add_argument("path", nargs="?", help="report file or reports dir (default: from config)")
    sp.add_argument("--level", choices=["error", "warn"],
                    help="override fail threshold (warn = warnings also fail)")
    sp.add_argument("--show-warnings", action="store_true",
                    help="print warnings even when they don't fail the lint")
    sp.set_defaults(func=_cmd_lint)

    sp = sub.add_parser("build", help="render md -> html + index")
    sp.add_argument("path", nargs="?", help="reports dir (default: from config)")
    sp.add_argument("--out", help="output directory (default: in place)")
    sp.set_defaults(func=_cmd_build)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
