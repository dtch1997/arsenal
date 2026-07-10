"""Unit tests for the tunnel provider seam (no real tunnels are spawned)."""
from __future__ import annotations

import pytest

from lobby.tunnel import (
    PROVIDERS,
    Cloudflare,
    LocalhostRun,
    Tunnel,
    TunnelError,
    _resolve,
    parse_tunnel_url,
    register_provider,
    tunnel,
)


def test_builtin_providers_registered():
    for key in ("cloudflare", "cloudflared", "localhost.run", "lhr", "ngrok"):
        assert key in PROVIDERS


def test_resolve_by_name_alias_and_instance():
    assert _resolve("cloudflared") is PROVIDERS["cloudflare"]
    inst = Cloudflare()
    assert _resolve(inst) is inst
    with pytest.raises(TunnelError, match="unknown tunnel provider"):
        _resolve("teleport")


def test_cloudflare_argv_and_extract():
    cf = Cloudflare()
    assert cf.argv(1234) == ["cloudflared", "tunnel", "--no-autoupdate",
                             "--url", "http://127.0.0.1:1234"]
    log = "INF ... https://big-hub-index-pages.trycloudflare.com registered"
    assert cf.extract(log) == "https://big-hub-index-pages.trycloudflare.com"
    assert cf.extract("no url here") is None


def test_localhostrun_argv_forwards_port():
    assert "80:localhost:9999" in " ".join(LocalhostRun().argv(9999))


def test_missing_binary_raises_tunnel_error():
    class Ghost(Tunnel):
        name = "ghost"
        binary = "definitely-not-a-real-binary-xyz"

        def argv(self, port):
            return [self.binary]

    with pytest.raises(TunnelError, match="not found on PATH"):
        Ghost().open(8000)


def test_register_custom_provider_and_tunnel_dispatch():
    calls = {}

    class Fake(Tunnel):
        name = "fake"

        def open(self, port, *, wait=40.0):
            calls["port"] = port
            return "https://fake.example", lambda: None

    register_provider(Fake(), "fk")
    try:
        url, stop = tunnel(8123, provider="fk")
        assert url == "https://fake.example" and calls["port"] == 8123
        stop()
    finally:
        PROVIDERS.pop("fake", None)
        PROVIDERS.pop("fk", None)


def test_parse_tunnel_url():
    assert parse_tunnel_url("x https://a-b.trycloudflare.com y") == "https://a-b.trycloudflare.com"
    assert parse_tunnel_url("nothing") is None
