"""Serve a runs/ directory (its status.html) over a Cloudflare quick tunnel.

`live_dashboard` only *writes* status.html; this is the missing "and now look at it
from anywhere" half. It stands up a local `http.server` over the directory and a
`cloudflared` quick tunnel in front of it, then returns the public URL.

    from stagehand import serve
    url, stop = serve("runs")           # -> https://<random>.trycloudflare.com/status.html
    ...                                  # watch it; the <meta refresh> auto-updates
    stop()                               # tear down server + tunnel

The only dependency is the `cloudflared` binary on PATH — a binary, not a pip
package — and it's touched only when you call `serve()`, so importing stagehand
never requires it. Raises RuntimeError if cloudflared is missing or no tunnel URL
appears in time.
"""
from __future__ import annotations
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def parse_tunnel_url(text: str):
    """Pull the public trycloudflare URL out of cloudflared's log output (or None)."""
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(directory, *, entry="status.html", port=None, wait=40.0):
    """Serve `directory` over http.server + a Cloudflare quick tunnel.

    Returns `(url, stop)`: `url` is the public link to `entry` within the directory
    (pass `entry=None`/`""` to link the directory root); `stop()` tears down both the
    HTTP server and the tunnel. Both run as child processes of the caller. Requires
    the `cloudflared` binary on PATH; raises RuntimeError if it's missing or no URL
    appears within `wait` seconds.
    """
    if not shutil.which("cloudflared"):
        raise RuntimeError("cloudflared not found on PATH — install it to serve the dashboard.")
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    port = port or _free_port()

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(d)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    log = Path(tempfile.mkstemp(prefix="stagehand-cf-", suffix=".log")[1])
    logf = open(log, "wb")
    tunnel = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=logf, stderr=subprocess.STDOUT)

    def stop():
        for p in (tunnel, server):
            if p.poll() is None:
                p.terminate()
        logf.close()
        log.unlink(missing_ok=True)

    url, deadline = None, time.time() + wait
    while time.time() < deadline:
        if tunnel.poll() is not None:           # tunnel died before printing a URL
            break
        url = parse_tunnel_url(log.read_text(errors="replace"))
        if url:
            break
        time.sleep(0.5)

    if not url:
        tail = log.read_text(errors="replace")[-600:]
        stop()
        raise RuntimeError(f"cloudflared produced no URL within {wait:g}s.\n--- log tail ---\n{tail}")

    full = url + ("/" + entry if entry else "/")
    return full, stop
