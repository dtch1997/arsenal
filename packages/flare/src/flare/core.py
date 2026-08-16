"""flare core — stamp context, spool a record, page Daniel over Slack.

Stdlib only (urllib.request, tomllib, json) so this runs via ``uvx flare`` on a
bare pod with no other dependencies. A distress channel must never crash its
caller: transport failures are swallowed (spooled + warned), and ``send`` always
returns the record and never raises on a Slack/network problem.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tomllib
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEVERITIES = ("info", "warn", "page")

# identical (msg, source) already paged within this window → suppress the repost
SPAM_WINDOW = timedelta(minutes=10)


def _home() -> Path:
    return Path(os.environ.get("FLARE_HOME") or Path.home())


def log_path() -> Path:
    """The JSONL spool. Override the whole location with ``FLARE_LOG``."""
    override = os.environ.get("FLARE_LOG")
    return Path(override) if override else _home() / ".flare" / "log.jsonl"


def config_path() -> Path:
    override = os.environ.get("FLARE_CONFIG")
    return Path(override) if override else _home() / ".config" / "flare" / "config.toml"


def webhook_url() -> str | None:
    """Resolve the Slack incoming-webhook URL: env first, then config.toml."""
    env = os.environ.get("FLARE_WEBHOOK")
    if env:
        return env
    path = config_path()
    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            return None
        url = (data.get("slack") or {}).get("webhook_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _git_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    branch = out.stdout.strip()
    return branch or None


def _context(now: datetime) -> dict:
    cwd = os.getcwd()
    return {
        "ts": now.isoformat(),
        "host": socket.gethostname(),
        "cwd": cwd,
        "git_branch": _git_branch(cwd),
        "session_id": os.environ.get("CLAUDE_SESSION_ID"),
        "task_id": os.environ.get("CONCIERGE_TASK_ID"),
    }


def _read_log() -> list[dict]:
    path = log_path()
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _append_log(record: dict) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _recently_sent(msg: str, source: str | None, now: datetime) -> bool:
    """True if an identical (msg, source) was posted to Slack within SPAM_WINDOW."""
    cutoff = now - SPAM_WINDOW
    for rec in _read_log():
        if not rec.get("sent") or rec.get("suppressed"):
            continue
        if rec.get("msg") != msg or rec.get("source") != source:
            continue
        try:
            ts = datetime.fromisoformat(rec["ts"])
        except (KeyError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            return True
    return False


def format_slack(record: dict) -> str:
    """`[sev] msg` headline + a context line (host · cwd · branch · session/task)."""
    headline = f"[{record['sev']}] {record['msg']}"
    bits = [f"host {record['host']}", f"cwd {record['cwd']}"]
    if record.get("git_branch"):
        bits.append(f"branch {record['git_branch']}")
    if record.get("session_id"):
        bits.append(f"session {record['session_id']}")
    if record.get("task_id"):
        bits.append(f"task {record['task_id']}")
    return headline + "\n" + " · ".join(bits)


def _post_slack(url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def send(
    msg: str,
    sev: str = "info",
    source: str | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Page Daniel and spool a record; return the record. Never raises on transport.

    ``now`` injects the clock (used by the spam guard and the timestamp) so tests
    can drive it deterministically.
    """
    if sev not in SEVERITIES:
        raise ValueError(f"sev must be one of {SEVERITIES}, got {sev!r}")
    now = now or datetime.now(timezone.utc)

    record = {"sev": sev, "msg": msg, "source": source, **_context(now)}
    record["sent"] = False
    record["suppressed"] = False

    url = webhook_url()
    if url is None:
        _append_log(record)
        print("no webhook configured; spooled only")
        return record

    if _recently_sent(msg, source, now):
        record["suppressed"] = True
        _append_log(record)
        return record

    try:
        _post_slack(url, format_slack(record))
        record["sent"] = True
    except Exception as e:  # transport must never crash the caller
        print(f"flare: webhook post failed ({e}); spooled only", file=sys.stderr)

    _append_log(record)
    return record
