"""Thin CLI mirroring the Python API: ``ferry push|pull|ls|size|remotes|doctor|install-rclone`` + ``ferry cas ...``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from ferry.core import (
    RcloneError,
    RcloneNotFound,
    _rclone_bin,
    ensure_rclone,
    listremotes,
    ls,
    pull,
    push,
    size,
)


def _add_transfer_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mirror", action="store_true", help="rclone sync (deletes extras on dest) instead of additive copy")
    p.add_argument("--dry-run", action="store_true", help="show what would change, transfer nothing")
    p.add_argument("--exclude", action="append", default=[], metavar="PATTERN", help="exclude pattern (repeatable)")
    p.add_argument("--include", action="append", default=[], metavar="PATTERN", help="include pattern (repeatable)")
    p.add_argument("--transfers", type=int, default=None, help="parallel file transfers")
    p.add_argument("--checkers", type=int, default=None, help="parallel checkers")


def _cas(args: argparse.Namespace) -> int:
    from ferry.cas import Client  # lazy: needs the [gcs] extra only here

    client = Client(bucket=args.bucket, prefix=args.prefix, project=args.project)
    if args.cas_cmd == "upload":
        print(client.upload(args.path))
        return 0
    if args.cas_cmd == "download":
        client.download(args.id, args.dest)
        print(args.dest)
        return 0
    if args.cas_cmd == "exists":
        ok = client.exists(args.id)
        print("true" if ok else "false")
        return 0 if ok else 1
    if args.cas_cmd == "rm":
        existed = client.delete(args.id)
        print("deleted" if existed else "not found")
        return 0 if existed else 1
    if args.cas_cmd == "uri":
        print(client.uri(args.id))
        return 0
    return 2


def _doctor(endpoint: str | None) -> int:
    """Preflight: binary, remotes, env credentials, and (optionally) one endpoint."""
    failed = False

    try:
        binary = _rclone_bin()
        version = subprocess.run(
            [binary, "version"], capture_output=True, text=True
        ).stdout.splitlines()[0]
        print(f"rclone      {binary} ({version})")
    except RcloneNotFound:
        print("rclone      NOT FOUND — run `ferry install-rclone`")
        return 1

    remotes = listremotes()
    print(f"remotes     {', '.join(remotes) if remotes else '(none — fine: gs:// and s3:// URLs auth from the environment)'}")

    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    gcs_cred = (
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or (str(adc) if adc.is_file() else None)
    )
    print(f"gcs creds   {gcs_cred or 'none found (gs:// URLs need GOOGLE_APPLICATION_CREDENTIALS, gcloud ADC, or instance metadata)'}")

    aws_cred = (
        "env (AWS_ACCESS_KEY_ID)" if os.environ.get("AWS_ACCESS_KEY_ID")
        else str(Path.home() / ".aws" / "credentials")
        if (Path.home() / ".aws" / "credentials").is_file()
        else None
    )
    print(f"s3 creds    {aws_cred or 'none found (s3:// URLs need AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or ~/.aws/credentials)'}")

    if endpoint:
        try:
            entries = ls(endpoint, flags=["--max-depth", "1"])
            print(f"endpoint    {endpoint} OK ({len(entries)} entries at top level)")
        except RcloneError as e:
            tail = (e.stderr or "").strip().splitlines()
            print(f"endpoint    {endpoint} FAILED (rclone exit {e.returncode})")
            for line in tail[-5:]:
                print(f"            {line}")
            failed = True

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ferry", description="Pythonic push/pull over rclone.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="local -> remote")
    p_push.add_argument("local")
    p_push.add_argument("remote")
    _add_transfer_flags(p_push)

    p_pull = sub.add_parser("pull", help="remote -> local")
    p_pull.add_argument("remote")
    p_pull.add_argument("local")
    _add_transfer_flags(p_pull)

    p_ls = sub.add_parser("ls", help="list entries under an endpoint")
    p_ls.add_argument("endpoint")

    p_size = sub.add_parser("size", help="object count and total bytes under an endpoint")
    p_size.add_argument("endpoint")

    sub.add_parser("remotes", help="list configured rclone remotes")

    p_doc = sub.add_parser("doctor", help="preflight: binary, credentials, optional endpoint check")
    p_doc.add_argument("endpoint", nargs="?", default=None, help="endpoint to test-list, e.g. gs://bucket/prefix/")

    sub.add_parser("install-rclone", help="download the static rclone binary to ~/.local/bin (no sudo)")

    p_cas = sub.add_parser("cas", help="content-addressed GCS store (ferry.cas)")
    p_cas.add_argument("--bucket", default=None, help="override GCS bucket")
    p_cas.add_argument("--prefix", default=None, help="override object key prefix")
    p_cas.add_argument("--project", default=None, help="GCP project for the client")
    cas_sub = p_cas.add_subparsers(dest="cas_cmd", required=True)
    c_up = cas_sub.add_parser("upload", help="upload a file; prints its id")
    c_up.add_argument("path")
    c_down = cas_sub.add_parser("download", help="download a file by id")
    c_down.add_argument("id")
    c_down.add_argument("dest")
    c_exists = cas_sub.add_parser("exists", help="exit 0 if id exists, 1 otherwise")
    c_exists.add_argument("id")
    c_rm = cas_sub.add_parser("rm", help="delete a file by id")
    c_rm.add_argument("id")
    c_uri = cas_sub.add_parser("uri", help="print the gs:// URI for an id")
    c_uri.add_argument("id")

    args = parser.parse_args(argv)

    if args.cmd == "cas":
        return _cas(args)

    try:
        if args.cmd == "remotes":
            for name in listremotes():
                print(name)
            return 0
        if args.cmd == "doctor":
            return _doctor(args.endpoint)
        if args.cmd == "install-rclone":
            print(ensure_rclone())
            return 0
        if args.cmd == "ls":
            for entry in ls(args.endpoint):
                print(entry)
            return 0
        if args.cmd == "size":
            info = size(args.endpoint)
            gib = info["bytes"] / (1 << 30)
            print(f"{info['count']} objects, {info['bytes']} bytes ({gib:.2f} GiB)")
            return 0

        common = dict(
            mirror=args.mirror,
            dry_run=args.dry_run,
            excludes=args.exclude,
            includes=args.include,
            transfers=args.transfers,
            checkers=args.checkers,
        )
        if args.cmd == "push":
            push(args.local, args.remote, **common)
        elif args.cmd == "pull":
            pull(args.remote, args.local, **common)
        return 0
    except RcloneNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 127
    except RcloneError as e:
        print(f"rclone failed (exit {e.returncode})", file=sys.stderr)
        for line in (e.stderr or "").strip().splitlines()[-5:]:
            print(f"  {line}", file=sys.stderr)
        return e.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
