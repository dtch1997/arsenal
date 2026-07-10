"""Put a public URL in front of a local port — behind a pluggable provider seam.

Absorbed from the retired `marquee` library. `tunnel(port)` returns
`(url, stop)`. A **provider** is the swappable backend; each one knows how to
spawn its tunnel and recover the public URL. The default flow is "spawn
subprocess → scan its output for the URL (with a timeout) → hold it alive →
SIGTERM on stop"; ngrok overrides it to poll its local agent API.

    from lobby.tunnel import tunnel
    url, stop = tunnel(8000)                           # cloudflared → https://….trycloudflare.com
    url, stop = tunnel(8000, provider="localhost.run") # zero-install ssh fallback
    ...
    stop()

Pure stdlib; a provider only needs its own tool on PATH (a binary or `ssh`),
touched solely when you call it. The hub daemon uses this to put its one
public URL in front of the whole lobby.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


class TunnelError(RuntimeError):
    """A tunnel could not be established (tool missing, or no URL in time)."""


# --- providers ------------------------------------------------------------- #
class Tunnel:
    """A tunnel provider. Subclass and set `name`/`binary`/`url_re` + `argv()`, or
    override `open()` for a different URL-discovery strategy (see `Ngrok`)."""
    name = "tunnel"
    binary: str | None = None          # executable that must be on PATH
    url_re: re.Pattern | None = None   # how to find the public URL in the tool's output

    def argv(self, port: int) -> list[str]:
        raise NotImplementedError

    def extract(self, text: str) -> str | None:
        m = self.url_re.search(text) if self.url_re else None
        return m.group(0) if m else None

    def open(self, port: int, *, wait: float = 40.0):
        """Spawn the tunnel, scan its log for the URL, return `(url, stop)`."""
        if self.binary and not shutil.which(self.binary):
            raise TunnelError(
                f"{self.binary!r} not found on PATH — install it to use the "
                f"{self.name!r} tunnel provider.")
        log = Path(tempfile.mkstemp(prefix=f"lobby-{self.name}-", suffix=".log")[1])
        logf = open(log, "wb")
        proc = subprocess.Popen(self.argv(port), stdout=logf, stderr=subprocess.STDOUT)

        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            logf.close()
            log.unlink(missing_ok=True)

        url, deadline = None, time.time() + wait
        while time.time() < deadline:
            if proc.poll() is not None:        # died before printing a URL
                break
            url = self.extract(log.read_text(errors="replace"))
            if url:
                break
            time.sleep(0.5)
        if not url:
            tail = log.read_text(errors="replace")[-600:]
            stop()
            raise TunnelError(
                f"{self.name}: no public URL within {wait:g}s.\n--- log tail ---\n{tail}")
        return url, stop


class Cloudflare(Tunnel):
    """Cloudflare quick tunnel — zero account, HTTPS. Needs the `cloudflared` binary."""
    name = "cloudflare"
    binary = "cloudflared"
    url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def argv(self, port):
        return ["cloudflared", "tunnel", "--no-autoupdate",
                "--url", f"http://127.0.0.1:{port}"]


class LocalhostRun(Tunnel):
    """localhost.run — zero account, zero install (uses the `ssh` you already have)."""
    name = "localhost.run"
    binary = "ssh"
    url_re = re.compile(r"https://[a-z0-9-]+\.lhr\.life")

    def argv(self, port):
        return ["ssh", "-T",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=30",
                "-R", f"80:localhost:{port}", "nokey@localhost.run"]


class Ngrok(Tunnel):
    """ngrok — most reliable, but needs a (free) account + `ngrok config
    add-authtoken`. The public URL is read from the local agent API, not scraped."""
    name = "ngrok"
    binary = "ngrok"

    def open(self, port, *, wait=40.0, api="http://127.0.0.1:4040/api/tunnels"):
        if not shutil.which("ngrok"):
            raise TunnelError("'ngrok' not found on PATH — install it to use the "
                              "'ngrok' tunnel provider.")
        proc = subprocess.Popen(["ngrok", "http", str(port), "--log", "stderr"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        url, deadline = None, time.time() + wait
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(api, timeout=2) as r:
                    tunnels = json.loads(r.read()).get("tunnels") or []
                https = [t for t in tunnels if str(t.get("public_url", "")).startswith("https")]
                pick = https or tunnels
                if pick:
                    url = pick[0]["public_url"]
                    break
            except Exception:
                pass                            # agent API not up yet
            time.sleep(0.5)
        if not url:
            stop()
            raise TunnelError(
                f"ngrok: no public URL within {wait:g}s — is your authtoken set "
                f"(`ngrok config add-authtoken …`)?")
        return url, stop


# --- registry -------------------------------------------------------------- #
PROVIDERS: dict[str, Tunnel] = {}


def register_provider(provider: Tunnel, *aliases: str):
    """Register a provider under its `name` (and any extra `aliases`)."""
    PROVIDERS[provider.name] = provider
    for a in aliases:
        PROVIDERS[a] = provider


register_provider(Cloudflare(), "cloudflared")
register_provider(LocalhostRun(), "localhostrun", "lhr")
register_provider(Ngrok())


def _resolve(provider) -> Tunnel:
    if isinstance(provider, Tunnel):
        return provider
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise TunnelError(
            f"unknown tunnel provider {provider!r}; "
            f"known: {sorted(PROVIDERS)}") from None


# --- public API ------------------------------------------------------------ #
def tunnel(port, *, provider="cloudflare", wait=40.0):
    """Put a public URL in front of `127.0.0.1:port`. Returns `(url, stop)`;
    `provider` is a name (see `PROVIDERS`) or a `Tunnel` instance."""
    return _resolve(provider).open(int(port), wait=wait)


def parse_tunnel_url(text: str):
    """Pull a Cloudflare quick-tunnel URL out of log text (or None). Kept for
    back-compat with code that scraped cloudflared output directly."""
    return Cloudflare().extract(text)
