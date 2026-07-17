"""Relay e2e, fully local: browser -> relay_httpd byte pump -> foyer server.

Exercises the whole stable-URL path except RunPod itself: control endpoints,
target publishing, plain HTTP forwarding, and — the part that matters — a
websocket terminal round-trip *through* the relay.
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

SESSION = "foyer-relay-pytest"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.2)
    pytest.fail(f"port {port} did not come up")


@pytest.fixture()
def stack(tmp_path):
    """foyer server + relay pump, relay targeting the foyer server."""
    env = {
        **os.environ,
        "FOYER_HOME": str(tmp_path / "foyer-home"),
        "FOYER_TMUX": f"tmux -L foyer-relay-pytest-{os.getpid()}",
    }
    tmux = env["FOYER_TMUX"].split()
    subprocess.run([*tmux, "new-session", "-d", "-s", SESSION, "-c", str(tmp_path)],
                   check=True)
    foyer_port, relay_port = _free_port(), _free_port()
    relay_holder = {}
    foyer_proc = subprocess.Popen(
        [sys.executable, "-m", "foyer.cli", "serve",
         "--port", str(foyer_port), "--no-tunnel"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    relay_env = {**env, "RELAY_PORT": str(relay_port), "RELAY_TOKEN": "relay-secret",
                 "RELAY_TARGET_FILE": str(tmp_path / "relay_target.json")}
    relay_proc = subprocess.Popen(
        [sys.executable, "-m", "foyer.relay_httpd"],
        env=relay_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    _wait_port(foyer_port)
    _wait_port(relay_port)
    relay_holder["proc"] = relay_proc

    def restart_relay():
        relay_holder["proc"].terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            relay_holder["proc"].wait(timeout=10)
        relay_holder["proc"] = subprocess.Popen(
            [sys.executable, "-m", "foyer.relay_httpd"],
            env=relay_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        _wait_port(relay_port)

    token = open(os.path.join(env["FOYER_HOME"], "token")).read().strip()
    try:
        yield {
            "relay": f"http://127.0.0.1:{relay_port}",
            "target": f"http://127.0.0.1:{foyer_port}",
            "token": token,
            "restart_relay": restart_relay,
        }
    finally:
        for p in (relay_holder["proc"], foyer_proc):
            p.terminate()
        for p in (relay_holder["proc"], foyer_proc):
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=10)
        subprocess.run([*tmux, "kill-server"], capture_output=True)


def test_relay_forwarding_and_websocket(stack):
    aiohttp = pytest.importorskip("aiohttp")
    relay, target, token = stack["relay"], stack["target"], stack["token"]

    async def run():
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            # control surface
            r = await s.get(relay + "/_foyer/ping")
            j = await r.json()
            assert j["app"] == "foyer-relay" and j["target_set"] is False

            # no target yet -> 503, not a hang
            r = await s.get(relay + "/")
            assert r.status == 503

            # bad token can't switch the target
            r = await s.post(relay + "/_foyer/target", json={"url": target},
                             headers={"Authorization": "Bearer wrong"})
            assert r.status == 401

            r = await s.post(relay + "/_foyer/target", json={"url": target},
                             headers={"Authorization": "Bearer relay-secret"})
            assert r.status == 200

            # forwarded auth flow: foyer's own token still guards everything
            r = await s.get(relay + "/", allow_redirects=False)
            assert r.status == 401
            r = await s.get(relay + f"/?t={token}", allow_redirects=False)
            assert r.status == 302
            r = await s.get(relay + "/api/sessions")
            names = [x["name"] for x in (await r.json())["sessions"]]
            assert SESSION in names

            # the crown jewel: a live terminal THROUGH the relay
            got = b""
            async with s.ws_connect(relay + f"/ws?target={SESSION}") as ws:
                await ws.send_str(json.dumps(
                    {"type": "resize", "cols": 120, "rows": 32}))
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(5):
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                break
                await asyncio.sleep(0.5)
                await ws.send_str(json.dumps(
                    {"type": "input", "data": "echo polo-$((3+4))\r"}))
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(8):
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                got += msg.data
                                if b"polo-7" in got:
                                    break
            assert b"polo-7" in got

            # ping exposes a target fingerprint for the devbox keeper
            from foyer.relay import target_fp
            r = await s.get(relay + "/_foyer/ping")
            j = await r.json()
            assert j["target_set"] is True and j["target_fp"] == target_fp(target)

            # the target survives a relay restart (persisted to disk)
            stack["restart_relay"]()
            r = await s.get(relay + "/_foyer/ping")
            j = await r.json()
            assert j["target_set"] is True and j["target_fp"] == target_fp(target)
            r = await s.get(relay + "/api/sessions")
            assert r.status == 200  # still forwarding after restart

            # unset target -> back to 503
            r = await s.post(relay + "/_foyer/target", json={"url": ""},
                             headers={"Authorization": "Bearer relay-secret"})
            assert r.status == 200
            r = await s.get(relay + "/")
            assert r.status == 503

    asyncio.run(run())
