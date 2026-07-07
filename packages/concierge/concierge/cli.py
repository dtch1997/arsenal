"""pool CLI — the API. HTTP (v1.5) mirrors these verbs 1:1."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import reconcile, runtime
from .records import ACTIVE, TERMINAL, Home, new_id, new_task


def _cfg(home: Home) -> dict:
    p = home.root / "config.yaml"
    if p.exists():
        try:
            import yaml
            return yaml.safe_load(p.read_text()) or {}
        except ImportError:
            print("[concierge] pyyaml not installed; ignoring config.yaml", file=sys.stderr)
    return {}


def cmd_submit(home, args):
    spec = Path(args.spec).read_text()
    tid = new_id()
    kind, _, arg = args.gate.partition(":")
    task = new_task(
        tid,
        title=args.title or Path(args.spec).stem,
        gate={"kind": kind, "arg": arg or None},
        budget={"usd": args.budget_usd, "wall_minutes": args.budget_minutes},
        workspace={"repo": args.repo, "base": args.base,
                   "branch": args.branch or f"pool/{tid}", "access": args.access},
        priority=args.priority,
        notify=args.notify.split(",") if args.notify else None,
        max_attempts=args.max_attempts,
    )
    home.spec_path(tid).write_text(spec)
    home.save(task)
    print(tid)


def cmd_status(home, args):
    if args.id:
        task = home.load(args.id)
        print(json.dumps(task, indent=2))
        msgs = home.messages(args.id)
        if msgs:
            print("\nmailbox:")
            for m in msgs:
                print(f"  [{m['ts']}] {m['from']}: {m['text']}")
        return
    rows = [(t["id"], t["status"], str(t["priority"]), str(len(t["attempts"])),
             f"${sum(a.get('cost_usd') or 0 for a in t['attempts']):.2f}", t["title"])
            for t in home.tasks()]
    if not rows:
        print("no tasks")
        return
    widths = [max(len(r[i]) for r in rows + [("id", "status", "pri", "att", "cost", "title")]) for i in range(6)]
    header = ("id", "status", "pri", "att", "cost", "title")
    for r in [header] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


def cmd_logs(home, args):
    task = home.load(args.id)
    for att in task["attempts"][-args.attempts:]:
        p = home.root / att["log"] / "agent.jsonl"
        print(f"--- attempt {att['n']} (pid {att['pid']}, {att['started']}) ---")
        if not p.exists():
            print("(no log yet)")
            continue
        for line in p.read_text(errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            rendered = _render(ev)
            if rendered:
                print(rendered)


def _render(ev):
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"· session {ev.get('session_id')} model={ev.get('model')}"
    if t == "assistant":
        out = []
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                arg = json.dumps(block.get("input", {}))[:120]
                out.append(f"→ {block.get('name')} {arg}")
        return "\n".join(out) or None
    if t == "result":
        status = "ERROR" if ev.get("is_error") else "ok"
        return f"■ result: {status} cost=${ev.get('total_cost_usd') or 0:.4f} turns={ev.get('num_turns')}"
    return None


def cmd_msg(home, args):
    home.load(args.id)  # validate id
    home.post(args.id, args.sender, args.text)
    print(f"posted to {args.id} mailbox (from {args.sender})")


def cmd_cancel(home, args):
    task = home.load(args.id)
    if task["status"] == "running":
        runtime.kill(task["attempts"][-1]["pid"])
    if task["status"] in TERMINAL:
        print(f"{args.id} already {task['status']}")
        return
    task["status"] = "cancelled"
    task["status_detail"] = "cancelled by user"
    home.save(task)
    print(f"{args.id} cancelled")


def cmd_await(home, args):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        task = home.load(args.id)
        if task["status"] in TERMINAL or (args.until_blocked and task["status"] == "blocked"):
            print(f"{args.id}: {task['status']} — {task['status_detail']}")
            for k, v in task["links"].items():
                if v:
                    print(f"  {k}: {v}")
            sys.exit(0 if task["status"] == "done" or args.until_blocked else 1)
        time.sleep(args.poll)
    print(f"{args.id}: timed out after {args.timeout}s (status={task['status']})")
    sys.exit(2)


def cmd_serve(home, args):
    reconcile.serve(home, _cfg(home) | vars_overrides(args),
                    exit_when_idle=args.exit_when_idle, interval=args.interval)


def vars_overrides(args) -> dict:
    out = {}
    if args.concurrency is not None:
        out["concurrency"] = args.concurrency
    return out


def cmd_rm(home, args):
    task = home.load(args.id)
    if task["status"] in ACTIVE:
        print(f"{args.id} is {task['status']}; cancel it first", file=sys.stderr)
        sys.exit(1)
    for p in (home.task_path(args.id), home.spec_path(args.id), home.mailbox_path(args.id)):
        p.unlink(missing_ok=True)
    for d in (home.workspace(args.id), home.root / "logs" / args.id):
        shutil.rmtree(d, ignore_errors=True)
    print(f"{args.id} removed")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="concierge", description="Worker pool over headless Claude sessions")
    ap.add_argument("--home", default=None, help="state dir (default: $CONCIERGE_HOME or ./concierge-home)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit", help="submit a task; prints its id")
    p.add_argument("spec", help="Markdown spec file")
    p.add_argument("--title", default=None)
    p.add_argument("--repo", default=None, help="git URL or local path; omit for a bare scratch workspace")
    p.add_argument("--base", default="main")
    p.add_argument("--branch", default=None, help="default: pool/<id>")
    p.add_argument("--access", default="readwrite", choices=["readwrite", "readonly"])
    p.add_argument("--gate", default="always", help="kind[:arg], e.g. shell_ok:'pytest -q', pr_open, file_exists:report.md")
    p.add_argument("--budget-usd", type=float, default=20.0, dest="budget_usd")
    p.add_argument("--budget-minutes", type=float, default=240.0, dest="budget_minutes")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--max-attempts", type=int, default=3, dest="max_attempts")
    p.add_argument("--notify", default=None, help="comma list, e.g. stdout,slack")
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("status", help="task table, or one task's detail")
    p.add_argument("id", nargs="?")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("logs", help="render a task's agent event stream")
    p.add_argument("id")
    p.add_argument("--attempts", type=int, default=1, help="how many recent attempts to show")
    p.set_defaults(fn=cmd_logs)

    p = sub.add_parser("msg", help="post to a task's mailbox")
    p.add_argument("id")
    p.add_argument("text")
    p.add_argument("--from", dest="sender", default="user", choices=["user", "worker"])
    p.set_defaults(fn=cmd_msg)

    p = sub.add_parser("cancel", help="kill and cancel a task")
    p.add_argument("id")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("rm", help="delete a non-active task's state")
    p.add_argument("id")
    p.set_defaults(fn=cmd_rm)

    p = sub.add_parser("await", help="block until a task reaches a terminal state; exit 0 iff done")
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--poll", type=float, default=2)
    p.add_argument("--until-blocked", action="store_true", dest="until_blocked",
                   help="also return (exit 0) when the task blocks on a question")
    p.set_defaults(fn=cmd_await)

    p = sub.add_parser("serve", help="run the reconciler daemon")
    p.add_argument("--interval", type=float, default=None)
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--exit-when-idle", action="store_true", dest="exit_when_idle")
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    args.fn(Home.locate(args.home), args)


if __name__ == "__main__":
    main()
