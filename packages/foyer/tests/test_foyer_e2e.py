"""End-to-end test: real server subprocess + a tmux session on a private socket.

Everything runs against a throwaway tmux server (`FOYER_TMUX="tmux -L …"`), so
the developer's own tmux sessions are never touched.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)

SESSION = "foyer-pytest"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def env(tmp_path, request):
    # Socket unique per test, not just per run: consecutive tests sharing a
    # socket race new-session against the previous teardown's kill-server.
    sock = f"foyer-pytest-{os.getpid()}-{abs(hash(request.node.name)) % 10**6}"
    e = {
        **os.environ,
        "FOYER_HOME": str(tmp_path / "foyer-home"),
        "FOYER_TMUX": f"tmux -L {sock}",
    }
    tmux = e["FOYER_TMUX"].split()
    subprocess.run([*tmux, "new-session", "-d", "-s", SESSION, "-c", str(tmp_path)],
                   check=True)
    try:
        yield e
    finally:
        subprocess.run([*tmux, "kill-server"], capture_output=True)


@pytest.fixture()
def server(env):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "foyer.cli", "serve",
         "--port", str(port), "--no-tunnel"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("foyer server did not come up")
    token_file = os.path.join(env["FOYER_HOME"], "token")
    token = open(token_file).read().strip()
    try:
        yield base, token
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_auth_sessions_terminal_notes(server):
    aiohttp = pytest.importorskip("aiohttp")
    base, token = server

    async def run():
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            r = await s.get(base + "/", allow_redirects=False)
            assert r.status == 401
            r = await s.get(base + f"/?t={token}", allow_redirects=False)
            assert r.status == 302
            r = await s.get(base + "/api/sessions")
            rows = (await r.json())["sessions"]
            mine = next(x for x in rows if x["name"] == SESSION)
            # regression: `capture-pane -t =name` (no colon) silently fails on
            # tmux 3.2a, leaving every preview empty
            assert mine["preview"], "sidebar preview should capture the pane"

            got = b""
            async with s.ws_connect(base + f"/ws?target={SESSION}") as ws:
                await ws.send_str(json.dumps(
                    {"type": "resize", "cols": 120, "rows": 32}))
                with contextlib.suppress(TimeoutError):  # wait for first paint
                    async with asyncio.timeout(5):
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                break
                await asyncio.sleep(0.5)
                await ws.send_str(json.dumps(
                    {"type": "input", "data": "echo marco-$((2+2))\r"}))
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(8):
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                got += msg.data
                                if b"marco-4" in got:
                                    break
            assert b"marco-4" in got

            r = await s.put(base + f"/api/notes/{SESSION}",
                            json={"text": "# note"})
            assert r.status == 200
            r = await s.get(base + f"/api/notes/{SESSION}")
            assert (await r.json())["text"] == "# note"

            r = await s.get(base + "/api/file", params={"path": "/etc/passwd"})
            assert r.status == 403

    asyncio.run(run())


def test_order_and_plot_root(server, env):
    aiohttp = pytest.importorskip("aiohttp")
    base, token = server
    tmux = env["FOYER_TMUX"].split()
    other = f"{SESSION}-b"
    subprocess.run([*tmux, "new-session", "-d", "-s", other], check=True)
    plot_dir = os.path.join(os.path.expanduser("~"), ".cache", "foyer-pytest-root")
    os.makedirs(plot_dir, exist_ok=True)

    async def run():
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            await s.get(base + f"/?t={token}")

            # manual order wins over activity order
            r = await s.put(base + "/api/order",
                            json={"names": [other, SESSION]})
            assert r.status == 200
            r = await s.get(base + "/api/sessions")
            names = [x["name"] for x in (await r.json())["sessions"]]
            assert names[:2] == [other, SESSION]

            # plot-root override: set, reject outside $HOME, reset
            r = await s.put(base + f"/api/plotroot/{SESSION}",
                            json={"root": plot_dir})
            assert r.status == 200 and (await r.json())["override"] is True
            r = await s.get(base + f"/api/plots?session={SESSION}")
            j = await r.json()
            assert j["root"] == plot_dir and j["override"] is True
            r = await s.put(base + f"/api/plotroot/{SESSION}",
                            json={"root": "/etc"})
            assert r.status == 400
            r = await s.put(base + f"/api/plotroot/{SESSION}",
                            json={"root": ""})
            assert r.status == 200 and (await r.json())["override"] is False

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(plot_dir, ignore_errors=True)


def test_thread_create_and_rename(server, env):
    aiohttp = pytest.importorskip("aiohttp")
    base, token = server
    ws_dir = os.path.join(os.path.expanduser("~"), ".cache", "foyer-pytest-ws")
    os.makedirs(ws_dir, exist_ok=True)
    # config: default workspace + a harmless command instead of `claude`
    with open(os.path.join(env["FOYER_HOME"], "config.json"), "w") as f:
        json.dump({"workspace": ws_dir, "command": "true"}, f)

    async def run():
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            await s.get(base + f"/?t={token}")

            r = await s.get(base + "/api/config")
            assert (await r.json())["workspace"] == ws_dir

            # auto-named thread lands in the configured workspace
            r = await s.post(base + "/api/threads", json={})
            assert r.status == 200
            made = (await r.json())["name"]
            assert made == "foyer-pytest-ws-1"
            r = await s.get(base + "/api/sessions")
            rows = {x["name"]: x for x in (await r.json())["sessions"]}
            assert rows[made]["cwd"] == ws_dir

            # duplicates and bad names are refused
            r = await s.post(base + "/api/threads", json={"name": made})
            assert r.status == 400
            r = await s.post(base + "/api/threads", json={"name": "bad:name"})
            assert r.status == 400
            r = await s.post(base + "/api/threads",
                             json={"name": "x", "dir": "/etc"})
            assert r.status == 400

            # rename migrates per-thread state (notes)
            await s.put(base + f"/api/notes/{made}", json={"text": "keep me"})
            r = await s.post(base + f"/api/threads/{made}/rename",
                             json={"name": "renamed-thread"})
            assert r.status == 200
            r = await s.get(base + "/api/sessions")
            names = [x["name"] for x in (await r.json())["sessions"]]
            assert "renamed-thread" in names and made not in names
            r = await s.get(base + "/api/notes/renamed-thread")
            assert (await r.json())["text"] == "keep me"
            r = await s.post(base + "/api/threads/nope-xyz/rename",
                             json={"name": "whatever"})
            assert r.status == 404

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(ws_dir, ignore_errors=True)
