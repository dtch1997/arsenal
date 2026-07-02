"""The ``cairn`` command-line interface — thin wrapper over Store.

Every command that emits data supports ``--json`` so agents can parse it;
human output is the default for interactive use.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import __version__
from .models import Issue, KNOWN_TYPES, MAX_PRIORITY, MIN_PRIORITY, Status
from .store import DEFAULT_PREFIX, CairnError, Store

_STATUS_TAG = {
    Status.OPEN.value: "open",
    Status.IN_PROGRESS.value: "wip ",
    Status.CLOSED.value: "done",
}


def _default_actor() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        return "agent"


# ---- formatting ----------------------------------------------------------
def _line(issue: Issue) -> str:
    tag = _STATUS_TAG.get(issue.status, issue.status)
    who = f" @{issue.assignee}" if issue.assignee else ""
    return f"{issue.id}  [{tag}] P{issue.priority} {issue.title}{who}"


def _full(issue: Issue, store: Store) -> str:
    lines = [
        f"{issue.id}  {issue.title}",
        f"  status:   {issue.status}",
        f"  priority: P{issue.priority}",
        f"  type:     {issue.type}",
    ]
    if issue.assignee:
        lines.append(f"  assignee: {issue.assignee}")
    if issue.labels:
        lines.append(f"  labels:   {', '.join(issue.labels)}")
    if issue.parent:
        lines.append(f"  parent:   {issue.parent}")
    if issue.blocked_by:
        parts = []
        for b in issue.blocked_by:
            state = store.get(b).status if store.exists(b) else "missing"
            parts.append(f"{b} ({state})")
        lines.append(f"  blocked_by: {', '.join(parts)}")
    lines.append(f"  created:  {issue.created}")
    lines.append(f"  updated:  {issue.updated}")
    if issue.closed_at:
        lines.append(f"  closed:   {issue.closed_at}")
    if issue.description:
        lines.append("")
        lines.append(issue.description)
    return "\n".join(lines)


def _emit_issue(issue: Issue, store: Store, as_json: bool) -> None:
    if as_json:
        print(json.dumps(issue.to_dict(), indent=2, sort_keys=True))
    else:
        print(_full(issue, store))


def _emit_list(issues: list[Issue], as_json: bool) -> None:
    if as_json:
        print(json.dumps([i.to_dict() for i in issues], indent=2, sort_keys=True))
    else:
        if not issues:
            print("(none)")
        for issue in issues:
            print(_line(issue))


# ---- command handlers ----------------------------------------------------
def _cmd_init(args) -> int:
    store = Store.init(args.root or ".", prefix=args.prefix)
    print(f"initialized {store.dir} (id prefix '{store.prefix}')")
    return 0


def _cmd_create(args) -> int:
    store = Store.discover()
    issue = store.create(
        title=args.title,
        description=args.description or "",
        priority=args.priority,
        type=args.type,
        assignee=args.assignee,
        labels=args.label,
    )
    if args.json:
        print(json.dumps(issue.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"created {issue.id}: {issue.title}")
    return 0


def _cmd_show(args) -> int:
    store = Store.discover()
    _emit_issue(store.get(args.id), store, args.json)
    return 0


def _cmd_list(args) -> int:
    store = Store.discover()
    issues = store.all()
    if args.status:
        issues = [i for i in issues if i.status == args.status]
    if args.type:
        issues = [i for i in issues if i.type == args.type]
    if args.assignee:
        issues = [i for i in issues if i.assignee == args.assignee]
    issues.sort(key=lambda i: (i.priority, i.created))
    _emit_list(issues, args.json)
    return 0


def _cmd_ready(args) -> int:
    store = Store.discover()
    _emit_list(store.ready(), args.json)
    return 0


def _cmd_update(args) -> int:
    store = Store.discover()
    changes = {
        "title": args.title,
        "description": args.description,
        "priority": args.priority,
        "type": args.type,
        "assignee": args.assignee,
        "status": args.status,
    }
    issue = store.update(args.id, **changes)
    _emit_issue(issue, store, args.json)
    return 0


def _cmd_claim(args) -> int:
    store = Store.discover()
    who = args.as_ or _default_actor()
    issue = store.claim(args.id, who)
    if args.json:
        print(json.dumps(issue.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"claimed {issue.id} as @{who} (in_progress)")
    return 0


def _cmd_close(args) -> int:
    store = Store.discover()
    closed = [store.close(i) for i in args.id]
    if args.json:
        print(json.dumps([i.to_dict() for i in closed], indent=2, sort_keys=True))
    else:
        for issue in closed:
            print(f"closed {issue.id}: {issue.title}")
    return 0


def _cmd_reopen(args) -> int:
    store = Store.discover()
    issue = store.reopen(args.id)
    print(f"reopened {issue.id}: {issue.title}")
    return 0


def _cmd_dep(args) -> int:
    store = Store.discover()
    if args.blocked_by:
        store.add_dep(args.id, args.blocked_by)
        print(f"{args.id} now blocked by {args.blocked_by}")
    if args.unblock:
        store.remove_dep(args.id, args.unblock)
        print(f"{args.id} no longer blocked by {args.unblock}")
    if args.parent is not None:
        store.set_parent(args.id, args.parent or None)
        print(f"{args.id} parent set to {args.parent or '(none)'}")
    if not (args.blocked_by or args.unblock or args.parent is not None):
        _emit_issue(store.get(args.id), store, args.json)
    return 0


def _cmd_remember(args) -> int:
    store = Store.discover()
    store.remember(args.text)
    print("remembered.")
    return 0


def _cmd_prime(args) -> int:
    store = Store.discover()
    memories = store.memories()
    ready = store.ready()
    if args.json:
        print(
            json.dumps(
                {
                    "workflow": _PRIME_WORKFLOW,
                    "ready": [i.to_dict() for i in ready],
                    "memory": [m["text"] for m in memories],
                },
                indent=2,
            )
        )
        return 0
    print(_PRIME_WORKFLOW)
    print()
    print(f"Ready now ({len(ready)}):")
    if not ready:
        print("  (nothing ready — check `cairn list` for blocked work)")
    for issue in ready[:20]:
        print(f"  {_line(issue)}")
    if memories:
        print()
        print("Project memory:")
        for mem in memories:
            print(f"  - {mem['text']}")
    return 0


_PRIME_WORKFLOW = """\
This project tracks work with cairn (a dependency-aware issue graph).
- `cairn ready` lists tasks with no open blockers — start there.
- `cairn claim <id>` takes a task (sets you as assignee, marks in_progress).
- `cairn close <id>` when done; `cairn create "Title"` to add work.
- `cairn dep <id> --blocked-by <other>` records dependencies.
- `cairn remember "insight"` stores a durable note; `cairn prime` replays it.
Do not keep a separate markdown TODO list — cairn is the source of truth."""


# ---- parser --------------------------------------------------------------
def _priority(value: str) -> int:
    iv = int(value)
    if not (MIN_PRIORITY <= iv <= MAX_PRIORITY):
        raise argparse.ArgumentTypeError(
            f"priority must be {MIN_PRIORITY}..{MAX_PRIORITY}"
        )
    return iv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cairn", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create a .cairn/ store in the current dir")
    sp.add_argument("--prefix", default=DEFAULT_PREFIX, help="id prefix (default: cn)")
    sp.add_argument("--root", help="directory to init in (default: cwd)")
    sp.set_defaults(func=_cmd_init)

    sp = sub.add_parser("create", help="create an issue")
    sp.add_argument("title")
    sp.add_argument("-d", "--description", default="")
    sp.add_argument("-p", "--priority", type=_priority, default=2)
    sp.add_argument("-t", "--type", default="task", help=f"one of {', '.join(KNOWN_TYPES)} (free-form)")
    sp.add_argument("-a", "--assignee")
    sp.add_argument("-l", "--label", action="append", default=[])
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_create)

    sp = sub.add_parser("show", help="show one issue in detail")
    sp.add_argument("id")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_show)

    sp = sub.add_parser("list", help="list issues (optionally filtered)")
    sp.add_argument("-s", "--status", choices=[s.value for s in Status])
    sp.add_argument("-t", "--type")
    sp.add_argument("-a", "--assignee")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("ready", help="list actionable issues (no open blockers)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_ready)

    sp = sub.add_parser("update", help="edit fields of an issue")
    sp.add_argument("id")
    sp.add_argument("--title")
    sp.add_argument("-d", "--description")
    sp.add_argument("-p", "--priority", type=_priority)
    sp.add_argument("-t", "--type")
    sp.add_argument("-a", "--assignee")
    sp.add_argument("-s", "--status", choices=[s.value for s in Status])
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_update)

    sp = sub.add_parser("claim", help="take a task (assignee + in_progress)")
    sp.add_argument("id")
    sp.add_argument("--as", dest="as_", help="actor name (default: current user)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_claim)

    sp = sub.add_parser("close", help="close one or more issues")
    sp.add_argument("id", nargs="+")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_close)

    sp = sub.add_parser("reopen", help="reopen a closed issue")
    sp.add_argument("id")
    sp.set_defaults(func=_cmd_reopen)

    sp = sub.add_parser("dep", help="add/remove dependencies or set parent")
    sp.add_argument("id")
    sp.add_argument("--blocked-by", dest="blocked_by", help="mark <id> blocked by this issue")
    sp.add_argument("--unblock", help="remove a blocked-by edge")
    sp.add_argument("--parent", help="set parent id (empty string clears)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_dep)

    sp = sub.add_parser("remember", help="store a durable project note")
    sp.add_argument("text")
    sp.set_defaults(func=_cmd_remember)

    sp = sub.add_parser("prime", help="print agent workflow context + memory")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_prime)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CairnError as exc:
        print(f"cairn: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
