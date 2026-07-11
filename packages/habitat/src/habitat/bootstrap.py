"""Pod bootstrap: the only code delivered via the docker start command.

Deliberately tiny (it travels base64-encoded inside the pod's start command)
and standalone. It has two jobs:

1. If no app has been pushed yet, serve a bare HTTP endpoint on $HABITAT_PORT
   that accepts a token-gated ``POST /api/code`` (tar.gz of an app directory
   with a ``server.py`` at its root) and answers ``GET /api/ping`` so the
   client can tell "bootstrap waiting" from "app running".
2. Supervise the app: run ``python3 <app>/server.py`` in a loop. The server
   exits with code 42 after swapping in new code via its own /api/code; any
   other exit is a crash and restarts after a pause.

The habitat client pushes the same tarball to the same endpoint whether it is
talking to this bootstrap or to the running server.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

APP_DIR = Path(os.environ.get("HABITAT_APP") or "/app/habitat_app")
PORT = int(os.environ.get("HABITAT_PORT") or 8080)
RESTART_EXIT_CODE = 42


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    for member in tar.getmembers():
        name = member.name
        if name.startswith(("/", "..")) or ".." in Path(name).parts:
            raise ValueError(f"unsafe path in archive: {name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported member type: {name!r}")
    tar.extractall(dest)


class BootstrapHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "habitat-bootstrap"
    got_code = False

    def log_message(self, fmt, *args):  # noqa: N802 - stdlib name
        pass

    def _json(self, obj: dict, code: int = 200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/ping":
            return self._json({"app": "habitat-bootstrap"})
        self._json({"error": "habitat bootstrap: push code to /api/code"}, 404)

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != "/api/code":
            return self._json({"error": "not found"}, 404)
        token = os.environ.get("HABITAT_TOKEN") or ""
        got = self.headers.get("Authorization") or ""
        if not (token and hmac.compare_digest(got, f"Bearer {token}")):
            return self._json({"error": "bearer token required"}, 401)
        incoming = APP_DIR.parent / f".{APP_DIR.name}-incoming"
        try:
            import shutil
            shutil.rmtree(incoming, ignore_errors=True)
            incoming.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                _safe_extract(tar, incoming)
            if not (incoming / "server.py").exists():
                raise ValueError("archive has no server.py at its root")
            shutil.rmtree(APP_DIR, ignore_errors=True)
            incoming.replace(APP_DIR)
        except (tarfile.TarError, ValueError, OSError) as e:
            return self._json({"error": f"bad archive: {e}"}, 400)
        BootstrapHandler.got_code = True
        self._json({"ok": True, "starting": True})


def wait_for_code() -> None:
    server = HTTPServer(("0.0.0.0", PORT), BootstrapHandler)
    server.timeout = 1
    print(f"habitat-bootstrap: waiting for code push on :{PORT}", flush=True)
    while not BootstrapHandler.got_code:
        server.handle_request()
    server.server_close()
    time.sleep(0.5)  # let the port free up before the app binds it


def main() -> None:
    APP_DIR.parent.mkdir(parents=True, exist_ok=True)
    while True:
        if not (APP_DIR / "server.py").exists():
            wait_for_code()
        rc = subprocess.call([sys.executable, str(APP_DIR / "server.py")])
        print(f"habitat-bootstrap: server exited rc={rc}", flush=True)
        if rc != RESTART_EXIT_CODE:
            time.sleep(3)  # crash-loop guard


if __name__ == "__main__":
    main()
