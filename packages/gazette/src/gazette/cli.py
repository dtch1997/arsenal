"""``gazette sweep|notes|status`` — see package docstring for the lane model."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import gh, notes as notes_mod, sweep as sweep_mod
from .config import load_config


def _collect(cfg):
    open_prs, merged, warnings = [], [], []
    since = datetime.now(timezone.utc) - timedelta(hours=cfg.notes_window_hours)
    for repo in cfg.github_repos:
        prs, w = gh.list_open_prs(repo)
        open_prs.extend(prs)
        warnings.extend(w)
        m, w2 = gh.list_merged_since(repo, since)
        merged.extend(m)
        warnings.extend(w2)
    return open_prs, merged, warnings


def cmd_sweep(args) -> int:
    cfg = load_config()
    report = sweep_mod.run(cfg, dry_run=args.dry_run)
    print(sweep_mod.format_report(report))
    return 0


def cmd_notes(args) -> int:
    cfg = load_config()
    now = datetime.now(timezone.utc)
    open_prs, merged, warnings = _collect(cfg)
    text = notes_mod.build_notes(cfg, now, open_prs, merged, warnings)
    path = notes_mod.spool_notes(now, text)
    print(text)
    if path:
        print(f"\n[spooled → {path}]", file=sys.stderr)
    if args.flare:
        try:
            import flare

            flare.send(notes_mod.digest(cfg, now, open_prs, merged), sev="info", source="gazette")
        except Exception as e:  # a notes cron must not crash on transport
            print(f"[flare failed: {e}]", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    now = datetime.now(timezone.utc)
    open_prs, merged, _ = _collect(cfg)
    print(notes_mod.digest(cfg, now, open_prs, merged))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gazette", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="nightly merge pass over all configured repos")
    p_sweep.add_argument("--dry-run", action="store_true", help="decide and report, merge nothing")
    p_sweep.set_defaults(fn=cmd_sweep)

    p_notes = sub.add_parser("notes", help="render morning patch notes (spooled to ~/.gazette/notes/)")
    p_notes.add_argument("--flare", action="store_true", help="also send the digest via flare")
    p_notes.set_defaults(fn=cmd_notes)

    p_status = sub.add_parser("status", help="one-line digest")
    p_status.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
