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
def env(tmp_path):
    e = {
        **os.environ,
        "FOYER_HOME": str(tmp_path / "foyer-home"),
        "FOYER_TMUX": f"tmux -L foyer-pytest-{os.getpid()}",
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
            assert any(x["name"] == SESSION for x in rows)

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
        os.rmdir(plot_dir)
