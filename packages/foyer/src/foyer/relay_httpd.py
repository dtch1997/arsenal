#!/usr/bin/env python3
"""foyer relay — the pod-side half of foyer's stable URL.

Runs stdlib-only on a cheap always-on RunPod CPU pod, whose
``https://<pod-id>-8080.proxy.runpod.net`` address never changes. foyer's
actual location (an ephemeral quick-tunnel URL that changes on every devbox
restart) is *published* to this process, which then forwards every request
to it.

The forwarder is a deliberate byte pump, not an HTTP proxy: it reads the
request head, rewrites ``Host`` (and pins ``Connection: close`` for plain
requests, preserving ``Upgrade`` for websockets), opens TLS to the current
target, and from then on copies bytes in both directions until either side
hangs up. A websocket after its handshake is just bytes over TCP, so the
terminal works through this without the relay knowing what a websocket is.

Control surface (never forwarded):
  GET  /_foyer/ping             -> {"app": "foyer-relay", "target_set": bool}
  POST /_foyer/target           -> {"url": "..."} with Authorization: Bearer
                                   $RELAY_TOKEN; "" unsets the target.

Auth model: foyer's own token still guards everything behind the relay; the
relay token only guards target switching.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import threading
from urllib.parse import urlsplit

PORT = int(os.environ.get("RELAY_PORT", "8080"))
TOKEN = os.environ.get("RELAY_TOKEN", "")
_state = {"target": os.environ.get("RELAY_TARGET", "")}
_lock = threading.Lock()

MAX_HEAD = 65536
IDLE_TIMEOUT = 600  # generous: foyer's websockets heartbeat every 30s


def _respond(client: socket.socket, code: int, body: str,
             ctype: str = "text/plain; charset=utf-8") -> None:
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
              502: "Bad Gateway", 503: "Service Unavailable"}.get(code, "OK")
    data = body.encode()
    head = (f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n")
    try:
        client.sendall(head.encode() + data)
    except OSError:
        pass


def _read_head(client: socket.socket) -> tuple[bytes | None, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = client.recv(65536)
        except OSError:
            return None, b""
        if not chunk or len(buf) > MAX_HEAD:
            return None, b""
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _control(client: socket.socket, method: str, path: str,
             headers: dict[str, str], rest: bytes) -> None:
    if path == "/_foyer/ping":
        with _lock:
            tgt = _state["target"]
        return _respond(client, 200, json.dumps(
            {"app": "foyer-relay", "target_set": bool(tgt)}),
            "application/json")
    if path == "/_foyer/target" and method == "POST":
        auth = headers.get("authorization", "")
        if not TOKEN or auth != f"Bearer {TOKEN}":
            return _respond(client, 401, "bad relay token")
        need = int(headers.get("content-length") or 0)
        body = rest
        while len(body) < need:
            chunk = client.recv(65536)
            if not chunk:
                break
            body += chunk
        try:
            url = str(json.loads(body or b"{}").get("url", "")).strip()
        except json.JSONDecodeError:
            return _respond(client, 400, "body must be JSON {url}")
        if url and urlsplit(url).scheme not in ("http", "https"):
            return _respond(client, 400, "url must be http(s)")
        with _lock:
            _state["target"] = url
        return _respond(client, 200, json.dumps({"ok": True}),
                        "application/json")
    _respond(client, 400, "unknown control path")


def _connect(target: str) -> socket.socket:
    u = urlsplit(target)
    port = u.port or (443 if u.scheme == "https" else 80)
    raw = socket.create_connection((u.hostname, port), timeout=30)
    if u.scheme == "https":
        ctx = ssl.create_default_context()
        return ctx.wrap_socket(raw, server_hostname=u.hostname)
    return raw


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s, how in ((dst, socket.SHUT_WR), (src, socket.SHUT_RD)):
            try:
                s.shutdown(how)
            except OSError:
                pass


def _handle(client: socket.socket) -> None:
    upstream = None
    try:
        client.settimeout(IDLE_TIMEOUT)
        head, rest = _read_head(client)
        if head is None:
            return
        lines = head.decode("latin1").split("\r\n")
        try:
            method, path, _ = lines[0].split(" ", 2)
        except ValueError:
            return _respond(client, 400, "bad request line")
        headers: dict[str, str] = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            headers[k.strip().lower()] = v.strip()

        if path.startswith("/_foyer/"):
            return _control(client, method, path, headers, rest)

        with _lock:
            target = _state["target"]
        if not target:
            return _respond(client, 503,
                            "foyer relay: no target published — run "
                            "`foyer serve` on the devbox")
        try:
            upstream = _connect(target)
        except OSError:
            return _respond(client, 502,
                            "foyer relay: target unreachable — is "
                            "`foyer serve` running on the devbox?")
        u = urlsplit(target)
        out = [lines[0]]
        for ln in lines[1:]:
            k = ln.split(":", 1)[0].strip().lower()
            if k in ("host", "connection", "proxy-connection", "keep-alive"):
                continue
            out.append(ln)
        out.append(f"Host: {u.netloc}")
        conn = headers.get("connection", "")
        if "upgrade" in conn.lower():
            out.append(f"Connection: {conn}")  # websocket handshake intact
        else:
            out.append("Connection: close")  # one upstream conn per request
        upstream.settimeout(IDLE_TIMEOUT)
        upstream.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin1") + rest)

        down = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
        down.start()
        _pump(client, upstream)  # client -> upstream in this thread
        down.join()
    finally:
        for s in (upstream, client):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(64)
    print(f"foyer-relay: listening on :{PORT}", flush=True)
    while True:
        client, _ = srv.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
