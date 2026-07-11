"""habitat CLI: provision / deploy / status / backup / restore / seed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import client
from .client import HabitatError


def cmd_provision(args) -> None:
    config = client.provision(tz=args.tz)
    print(f"habitat is up: {config['url']}  (version {config['version']})")
    print(f"secret: {config['token']}")
    print(f"config: {client.HOME / 'config.json'}")


def cmd_deploy(args) -> None:
    config = client.load_config()
    ver = client.push_code(config["url"], config["token"])
    config["version"] = ver
    client.save_config(config)
    print(f"deployed version {ver} to {config['url']}")


def cmd_status(args) -> None:
    config = client.load_config()
    info = client.ping(config["url"])
    print(f"url:     {config['url']}")
    if not info:
        print("status:  NOT ANSWERING (pod stopped or rebuilding — try `habitat provision`)")
        sys.exit(1)
    print(f"status:  {info.get('app')} v{info.get('version', '?')}, "
          f"{info.get('habits', '?')} habits")
    latest = client.HOME / "backups" / "latest.json"
    if latest.exists():
        dump = json.loads(latest.read_text())
        print(f"backup:  {dump.get('exported_at')} "
              f"({len(dump.get('completions', []))} completions)")
    else:
        print("backup:  none yet — run `habitat backup`")


def cmd_backup(args) -> None:
    path = client.backup()
    dump = json.loads(path.read_text())
    print(f"backed up {len(dump['habits'])} habits, "
          f"{len(dump['completions'])} completions -> {path}")


def cmd_restore(args) -> None:
    result = client.restore(dump_file=Path(args.file) if args.file else None)
    print(f"restored {result['habits']} habits, {result['completions']} completions")


def cmd_seed(args) -> None:
    added = client.seed(Path(args.file))
    print(f"seeded {len(added)} habits" + (f": {', '.join(added)}" if added else
                                           " (all already present)"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="habitat",
                                     description="Habit tracker on a RunPod CPU pod.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provision", help="find-or-create the pod and deploy the app")
    p.add_argument("--tz", default="Europe/London", help="IANA timezone for 'today'")
    p.set_defaults(fn=cmd_provision)
    sub.add_parser("deploy", help="push the local app code to the pod").set_defaults(fn=cmd_deploy)
    sub.add_parser("status", help="pod + backup status").set_defaults(fn=cmd_status)
    sub.add_parser("backup", help="pull a JSON snapshot of all data").set_defaults(fn=cmd_backup)
    p = sub.add_parser("restore", help="push a snapshot back (after pod rebuild)")
    p.add_argument("file", nargs="?", help="dump file (default: backups/latest.json)")
    p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("seed", help="create habits from a JSON list")
    p.add_argument("file")
    p.set_defaults(fn=cmd_seed)
    args = parser.parse_args()
    try:
        args.fn(args)
    except HabitatError as e:
        print(f"habitat: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
