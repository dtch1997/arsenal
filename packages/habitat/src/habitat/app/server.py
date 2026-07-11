"""The habitat server: a single-user habit tracker with token-gated everything.

This file is deliberately standalone (stdlib only, no habitat imports): the
client ships the whole ``app/`` directory to the pod as a tarball and the
bootstrap runs it with a bare ``python3 server.py``. State is one SQLite file
under $HABITAT_DATA; the durable copy is the client's local mirror, pulled via
/api/export on a cron and re-pushed via /api/import after a pod rebuild.

    GET  /                    the app (index.html; shows login until authed)
    POST /api/login           {"token": ...} -> session cookie
    GET  /api/ping            liveness: app id, version, habit count   (public)
    GET  /api/summary         habits + recency/week/stats for the today view
    GET  /api/habit/<id>      one habit + all completion days (heatmap food)
    POST /api/habits          create habit
    POST /api/habits/<id>     update fields / archive / unarchive
    POST /api/toggle          {"habit_id": ..., "day": "YYYY-MM-DD"} toggle
    GET  /api/export          full JSON dump of the database
    POST /api/import          replace all state from a dump         (Bearer only)
    POST /api/code            tar.gz of a new app dir; swap + restart (Bearer only)

Auth: every non-public route accepts the shared secret as a ``habitat_token``
cookie (browser) or ``Authorization: Bearer`` header (CLI).
"""

from __future__ import annotations

import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import threading
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent
RESTART_EXIT_CODE = 42  # bootstrap restarts us; anything else is a crash
BACKFILL_DAYS = 60      # how far back the UI may toggle a completion


def data_dir() -> Path:
    d = Path(os.environ.get("HABITAT_DATA") or "/data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "habitat.db"


def _token() -> str:
    return os.environ.get("HABITAT_TOKEN") or ""


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("HABITAT_TZ") or "Europe/London")


def today() -> str:
    return datetime.now(tz()).date().isoformat()


def version() -> str:
    vf = APP_DIR / "VERSION"
    return vf.read_text().strip() if vf.exists() else "dev"


SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    cadence TEXT NOT NULL DEFAULT '',
    legacy_count INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS completions (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);
"""

_db_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def habit_row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


# -- stats ------------------------------------------------------------------

def week_days(end_day: str, n: int = 7) -> list[str]:
    end = datetime.fromisoformat(end_day).date()
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def summary(conn: sqlite3.Connection) -> dict:
    now_day = today()
    last7 = week_days(now_day)
    month_prefix = now_day[:7]
    monday = (datetime.fromisoformat(now_day).date()
              - timedelta(days=datetime.fromisoformat(now_day).date().weekday())).isoformat()
    habits = []
    for r in conn.execute("SELECT * FROM habits WHERE archived_at IS NULL ORDER BY sort_order, id"):
        h = habit_row(r)
        days = [x["day"] for x in conn.execute(
            "SELECT day FROM completions WHERE habit_id=? ORDER BY day", (r["id"],))]
        h["done_today"] = now_day in days
        h["last_done"] = days[-1] if days else None
        h["days_since"] = ((datetime.fromisoformat(now_day).date()
                            - datetime.fromisoformat(days[-1]).date()).days
                           if days else None)
        h["week"] = [d in set(days) for d in last7]
        h["count_30d"] = sum(1 for d in days if d > (
            datetime.fromisoformat(now_day).date() - timedelta(days=30)).isoformat())
        h["count_month"] = sum(1 for d in days if d.startswith(month_prefix))
        h["total"] = len(days) + h["legacy_count"]
        habits.append(h)
    wins_week = conn.execute(
        "SELECT COUNT(*) FROM completions WHERE day >= ? AND day <= ?",
        (monday, now_day)).fetchone()[0]
    wins_month = conn.execute(
        "SELECT COUNT(*) FROM completions WHERE day LIKE ?", (month_prefix + "%",)).fetchone()[0]
    wins_all = conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0]
    legacy_all = conn.execute(
        "SELECT COALESCE(SUM(legacy_count),0) FROM habits").fetchone()[0]
    n_archived = conn.execute(
        "SELECT COUNT(*) FROM habits WHERE archived_at IS NOT NULL").fetchone()[0]
    return {
        "today": now_day, "week_days": last7, "habits": habits,
        "wins_week": wins_week, "wins_month": wins_month,
        "wins_all": wins_all + legacy_all, "archived": n_archived,
    }


def habit_detail(conn: sqlite3.Connection, habit_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM habits WHERE id=?", (habit_id,)).fetchone()
    if not r:
        return None
    h = habit_row(r)
    days = [x["day"] for x in conn.execute(
        "SELECT day FROM completions WHERE habit_id=? ORDER BY day", (habit_id,))]
    now_day = today()
    by_month: dict[str, int] = {}
    for d in days:
        by_month[d[:7]] = by_month.get(d[:7], 0) + 1
    h["days"] = days
    h["today"] = now_day
    h["total"] = len(days) + h["legacy_count"]
    h["count_month"] = by_month.get(now_day[:7], 0)
    h["best_month"] = (max(by_month.items(), key=lambda kv: kv[1])
                       if by_month else None)
    h["backfill_days"] = BACKFILL_DAYS
    return h


# -- export / import --------------------------------------------------------

def export_dump(conn: sqlite3.Connection) -> dict:
    return {
        "app": "habitat",
        "format": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "habits": [habit_row(r) for r in conn.execute("SELECT * FROM habits ORDER BY id")],
        "completions": [habit_row(r) for r in
                        conn.execute("SELECT * FROM completions ORDER BY habit_id, day")],
    }


def import_dump(conn: sqlite3.Connection, dump: dict) -> dict:
    if dump.get("app") != "habitat" or "habits" not in dump:
        raise ValueError("not a habitat dump")
    with conn:
        conn.execute("DELETE FROM completions")
        conn.execute("DELETE FROM habits")
        for h in dump["habits"]:
            conn.execute(
                "INSERT INTO habits (id,name,emoji,description,cadence,legacy_count,"
                "sort_order,created_at,archived_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (h["id"], h["name"], h.get("emoji", ""), h.get("description", ""),
                 h.get("cadence", ""), h.get("legacy_count", 0), h.get("sort_order", 0),
                 h.get("created_at") or datetime.now(timezone.utc).isoformat(),
                 h.get("archived_at")))
        for c in dump["completions"]:
            conn.execute("INSERT INTO completions (habit_id,day,ts) VALUES (?,?,?)",
                         (c["habit_id"], c["day"], c["ts"]))
    return {"habits": len(dump["habits"]), "completions": len(dump["completions"])}


# -- code self-update (same contract as bootstrap.py) -------------------------

def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    for member in tar.getmembers():
        name = member.name
        if name.startswith(("/", "..")) or ".." in Path(name).parts:
            raise ValueError(f"unsafe path in archive: {name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported member type: {name!r}")
    tar.extractall(dest)


def swap_in_code(raw: bytes) -> None:
    incoming = APP_DIR.parent / f".{APP_DIR.name}-incoming"
    outgoing = APP_DIR.parent / f".{APP_DIR.name}-old"
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        _safe_extract(tar, incoming)
    if not (incoming / "server.py").exists():
        shutil.rmtree(incoming, ignore_errors=True)
        raise ValueError("archive has no server.py at its root")
    shutil.rmtree(outgoing, ignore_errors=True)
    APP_DIR.replace(outgoing)
    incoming.replace(APP_DIR)
    shutil.rmtree(outgoing, ignore_errors=True)


# -- http --------------------------------------------------------------------

class HabitatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "habitat"

    def log_message(self, fmt, *args):  # noqa: N802 - stdlib name
        pass

    # -- auth ---------------------------------------------------------------

    def _authed(self) -> bool:
        token = _token()
        if not token:
            return False
        header = self.headers.get("Authorization") or ""
        if header and hmac.compare_digest(header, f"Bearer {token}"):
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        got = cookie["habitat_token"].value if "habitat_token" in cookie else ""
        return bool(got) and hmac.compare_digest(got, token)

    def _bearer_authed(self) -> bool:
        token = _token()
        header = self.headers.get("Authorization") or ""
        return bool(token) and hmac.compare_digest(header, f"Bearer {token}")

    # -- routing --------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if path == "/api/ping":
            with _db_lock, db() as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM habits WHERE archived_at IS NULL").fetchone()[0]
            return self._json({"app": "habitat", "version": version(), "habits": n,
                               "authed": self._authed()})
        if path.startswith("/api/"):
            if not self._authed():
                return self._json({"error": "not authed"}, 401)
            if path == "/api/summary":
                with _db_lock, db() as conn:
                    return self._json(summary(conn))
            if path == "/api/export":
                with _db_lock, db() as conn:
                    return self._json(export_dump(conn))
            if path == "/api/archived":
                with _db_lock, db() as conn:
                    habits = []
                    for r in conn.execute(
                            "SELECT * FROM habits WHERE archived_at IS NOT NULL "
                            "ORDER BY sort_order, id"):
                        h = habit_row(r)
                        n = conn.execute(
                            "SELECT COUNT(*) FROM completions WHERE habit_id=?",
                            (r["id"],)).fetchone()[0]
                        h["total"] = n + h["legacy_count"]
                        habits.append(h)
                return self._json({"habits": habits})
            m = re.fullmatch(r"/api/habit/(\d+)", path)
            if m:
                with _db_lock, db() as conn:
                    detail = habit_detail(conn, int(m.group(1)))
                return self._json(detail) if detail else self._json({"error": "no such habit"}, 404)
            return self._json({"error": "not found"}, 404)
        return self._static(path)

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if path == "/api/login":
            return self._login(raw)
        if path == "/api/code":
            return self._code(raw)
        if path == "/api/import":
            if not self._bearer_authed():
                return self._json({"error": "bearer token required"}, 401)
            return self._import(raw)
        if not self._authed():
            return self._json({"error": "not authed"}, 401)
        if path == "/api/toggle":
            return self._toggle(raw)
        if path == "/api/habits":
            return self._create(raw)
        m = re.fullmatch(r"/api/habits/(\d+)", path)
        if m:
            return self._update(int(m.group(1)), raw)
        return self._json({"error": "not found"}, 404)

    # -- handlers -------------------------------------------------------------

    def _login(self, raw: bytes):
        try:
            got = json.loads(raw or b"{}").get("token") or ""
        except json.JSONDecodeError:
            got = ""
        token = _token()
        if not (token and got and hmac.compare_digest(got, token)):
            return self._json({"error": "wrong token"}, 401)
        cookie = (f"habitat_token={token}; Path=/; Max-Age=31536000; "
                  "HttpOnly; Secure; SameSite=Lax")
        return self._json({"ok": True}, extra={"Set-Cookie": cookie})

    def _toggle(self, raw: bytes):
        try:
            body = json.loads(raw)
            habit_id, day = int(body["habit_id"]), str(body.get("day") or today())
        except (json.JSONDecodeError, KeyError, ValueError):
            return self._json({"error": "bad body"}, 400)
        now_day = today()
        try:
            parsed = datetime.fromisoformat(day).date()
        except ValueError:
            return self._json({"error": "bad day"}, 400)
        earliest = datetime.fromisoformat(now_day).date() - timedelta(days=BACKFILL_DAYS)
        if day > now_day or parsed < earliest:
            return self._json({"error": f"day must be within the last {BACKFILL_DAYS} days"}, 400)
        with _db_lock, db() as conn:
            if not conn.execute("SELECT 1 FROM habits WHERE id=? AND archived_at IS NULL",
                                (habit_id,)).fetchone():
                return self._json({"error": "no such habit"}, 404)
            with conn:
                gone = conn.execute(
                    "DELETE FROM completions WHERE habit_id=? AND day=?",
                    (habit_id, day)).rowcount
                if not gone:
                    conn.execute(
                        "INSERT INTO completions (habit_id, day, ts) VALUES (?,?,?)",
                        (habit_id, day, datetime.now(timezone.utc).isoformat()))
        return self._json({"habit_id": habit_id, "day": day, "done": not gone})

    def _create(self, raw: bytes):
        try:
            body = json.loads(raw)
            name = str(body["name"]).strip()
            assert name
        except (json.JSONDecodeError, KeyError, AssertionError):
            return self._json({"error": "name required"}, 400)
        with _db_lock, db() as conn, conn:
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order),0)+1 FROM habits").fetchone()[0]
            cur = conn.execute(
                "INSERT INTO habits (name,emoji,description,cadence,legacy_count,"
                "sort_order,created_at) VALUES (?,?,?,?,?,?,?)",
                (name, str(body.get("emoji") or ""), str(body.get("description") or ""),
                 str(body.get("cadence") or ""), int(body.get("legacy_count") or 0),
                 order, datetime.now(timezone.utc).isoformat()))
        return self._json({"id": cur.lastrowid}, 201)

    def _update(self, habit_id: int, raw: bytes):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return self._json({"error": "bad body"}, 400)
        fields = {k: body[k] for k in
                  ("name", "emoji", "description", "cadence", "legacy_count", "sort_order")
                  if k in body}
        with _db_lock, db() as conn, conn:
            if not conn.execute("SELECT 1 FROM habits WHERE id=?", (habit_id,)).fetchone():
                return self._json({"error": "no such habit"}, 404)
            if body.get("archive") is True:
                fields["archived_at"] = datetime.now(timezone.utc).isoformat()
            if body.get("archive") is False:
                fields["archived_at"] = None
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                conn.execute(f"UPDATE habits SET {sets} WHERE id=?",  # noqa: S608
                             (*fields.values(), habit_id))
        return self._json({"ok": True})

    def _import(self, raw: bytes):
        try:
            dump = json.loads(raw)
            with _db_lock, db() as conn:
                counts = import_dump(conn, dump)
        except (json.JSONDecodeError, ValueError, KeyError, sqlite3.Error) as e:
            return self._json({"error": f"bad dump: {e}"}, 400)
        return self._json(counts)

    def _code(self, raw: bytes):
        if not self._bearer_authed():
            return self._json({"error": "bearer token required"}, 401)
        try:
            swap_in_code(raw)
        except (tarfile.TarError, ValueError, OSError) as e:
            return self._json({"error": f"bad archive: {e}"}, 400)
        self._json({"ok": True, "restarting": True})
        # Give the response a moment to flush, then let the bootstrap loop
        # start the new code. os._exit skips atexit/thread teardown on purpose.
        threading.Timer(0.5, lambda: os._exit(RESTART_EXIT_CODE)).start()

    # -- static ---------------------------------------------------------------

    def _static(self, path: str):
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/index.html": ("index.html", "text/html; charset=utf-8"),
                 "/manifest.webmanifest": ("manifest.webmanifest",
                                           "application/manifest+json"),
                 "/icon.svg": ("icon.svg", "image/svg+xml")}
        if path not in files:
            return self._json({"error": "not found"}, 404)
        name, ctype = files[path]
        target = APP_DIR / name
        if not target.exists():
            return self._json({"error": f"{name} missing from app bundle"}, 500)
        self._send(200, target.read_bytes(), ctype)

    # -- response helpers ------------------------------------------------------

    def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj: dict, code: int = 200, extra: dict | None = None):
        self._send(code, json.dumps(obj).encode(), "application/json", extra)


def main() -> None:
    port = int(os.environ.get("HABITAT_PORT") or 8080)
    data_dir()  # fail fast if the data dir is not writable
    if not _token():
        print("habitat: HABITAT_TOKEN not set — refusing to serve", flush=True)
        sys.exit(1)
    server = ThreadingHTTPServer(("0.0.0.0", port), HabitatHandler)
    print(f"habitat {version()}: db {db_path()} on 0.0.0.0:{port} (tz {tz().key})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
