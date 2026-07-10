from __future__ import annotations

import http.server
import json
import os
import signal
import threading

import pytest

from lobby import client, state


class EchoHandler(http.server.BaseHTTPRequestHandler):
    """Backend stand-in: echoes method/path/body as JSON; /redirect 302s to a root path."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _respond(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        payload = json.dumps(
            {"method": self.command, "path": self.path, "body": body}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Backend", "echo")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _respond


@pytest.fixture
def backend():
    """A live echo backend on a free port; yields the port."""
    servers = []

    def start() -> tuple[int, http.server.ThreadingHTTPServer]:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1], srv

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="session")
def hub(tmp_path_factory):
    """One tunnel-less hub daemon for the whole test session, on isolated state+port."""
    state_dir = tmp_path_factory.mktemp("lobby-state")
    port = state.free_port()
    old = {k: os.environ.get(k) for k in ("LOBBY_STATE_DIR", "LOBBY_PORT")}
    os.environ["LOBBY_STATE_DIR"] = str(state_dir)
    os.environ["LOBBY_PORT"] = str(port)
    info = client.ensure_hub(tunnel=False, wait=20)
    yield {"port": port, "state_dir": state_dir, "info": info}
    hub_rec = state.read_json(state.hub_path())
    if hub_rec and state.pid_alive(hub_rec.get("pid")):
        os.kill(hub_rec["pid"], signal.SIGTERM)
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
