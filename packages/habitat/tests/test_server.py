"""End-to-end tests against a live server.py subprocess (the real deployable)."""

from __future__ import annotations

import datetime
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SERVER = Path(__file__).parents[1] / "src" / "habitat" / "app" / "server.py"
TOKEN = "test-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    port = _free_port()
    data = tmp_path_factory.mktemp("data")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        env={**os.environ, "HABITAT_TOKEN": TOKEN, "HABITAT_PORT": str(port),
             "HABITAT_DATA": str(data), "HABITAT_TZ": "Europe/London"},
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            req(url + "/api/ping")
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("server did not come up")
    yield url
    proc.kill()


def req(url: str, body: dict | None = None, token: str | None = None,
        cookie: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    r = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    with urllib.request.urlopen(r) as res:
        return json.loads(res.read()), dict(res.headers)


def code_of(err: Exception) -> int:
    assert isinstance(err, urllib.error.HTTPError)
    return err.code


def test_ping_public(base):
    data, _ = req(base + "/api/ping")
    assert data["app"] == "habitat" and data["authed"] is False


def test_api_requires_auth(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        req(base + "/api/summary")
    assert code_of(e.value) == 401


def test_login_bad_token(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        req(base + "/api/login", {"token": "nope"})
    assert code_of(e.value) == 401


def test_login_sets_cookie_and_cookie_works(base):
    data, headers = req(base + "/api/login", {"token": TOKEN})
    assert data["ok"] and "habitat_token" in headers["Set-Cookie"]
    cookie = headers["Set-Cookie"].split(";")[0]
    data, _ = req(base + "/api/summary", cookie=cookie)
    assert "habits" in data


def test_habit_lifecycle(base):
    created, _ = req(base + "/api/habits",
                     {"name": "Juggle", "emoji": "🤹", "legacy_count": 5}, TOKEN)
    hid = created["id"]
    s, _ = req(base + "/api/summary", token=TOKEN)
    h = next(x for x in s["habits"] if x["id"] == hid)
    assert h["total"] == 5 and h["done_today"] is False and h["last_done"] is None

    r, _ = req(base + "/api/toggle", {"habit_id": hid, "day": s["today"]}, TOKEN)
    assert r["done"] is True
    s, _ = req(base + "/api/summary", token=TOKEN)
    h = next(x for x in s["habits"] if x["id"] == hid)
    weekday = datetime.date.fromisoformat(s["today"]).weekday()
    assert s["week_days"][weekday] == s["today"] and len(s["week_days"]) == 7
    assert h["done_today"] and h["total"] == 6 and h["week"][weekday] is True
    assert h["days_since"] == 0 and s["wins_week"] >= 1

    r, _ = req(base + "/api/toggle", {"habit_id": hid, "day": s["today"]}, TOKEN)
    assert r["done"] is False

    detail, _ = req(base + f"/api/habit/{hid}", token=TOKEN)
    assert detail["name"] == "Juggle" and detail["days"] == []


def test_toggle_rejects_future_and_ancient(base):
    created, _ = req(base + "/api/habits", {"name": "Time travel"}, TOKEN)
    for bad in ("2099-01-01", "2001-01-01"):
        with pytest.raises(urllib.error.HTTPError) as e:
            req(base + "/api/toggle", {"habit_id": created["id"], "day": bad}, TOKEN)
        assert code_of(e.value) == 400


def test_archive_and_archived_list(base):
    created, _ = req(base + "/api/habits", {"name": "Old thing"}, TOKEN)
    req(base + f"/api/habits/{created['id']}", {"archive": True}, TOKEN)
    s, _ = req(base + "/api/summary", token=TOKEN)
    assert all(h["id"] != created["id"] for h in s["habits"])
    arch, _ = req(base + "/api/archived", token=TOKEN)
    assert any(h["id"] == created["id"] for h in arch["habits"])
    req(base + f"/api/habits/{created['id']}", {"archive": False}, TOKEN)
    s, _ = req(base + "/api/summary", token=TOKEN)
    assert any(h["id"] == created["id"] for h in s["habits"])


def test_export_import_roundtrip(base):
    before, _ = req(base + "/api/export", token=TOKEN)
    assert before["app"] == "habitat" and before["habits"]
    # import requires Bearer (a cookie is not enough)
    _, headers = req(base + "/api/login", {"token": TOKEN})
    cookie = headers["Set-Cookie"].split(";")[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        req(base + "/api/import", before, cookie=cookie)
    assert code_of(e.value) == 401
    result, _ = req(base + "/api/import", before, TOKEN)
    assert result["habits"] == len(before["habits"])
    after, _ = req(base + "/api/export", token=TOKEN)
    assert after["habits"] == before["habits"]
    assert after["completions"] == before["completions"]


def test_index_served(base):
    r = urllib.request.Request(base + "/")
    with urllib.request.urlopen(r) as res:
        body = res.read().decode()
    assert "habitat" in body and "text/html" in res.headers["Content-Type"]
