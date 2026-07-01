"""Unit tests for the serve shim. The implementation now lives in the `marquee`
library (tested there); here we cover the lazy re-export: a clear error when
marquee is absent, and that calls delegate to it when present."""
import builtins
import sys

import pytest

from stagehand.serve import serve, parse_tunnel_url


def _hide_marquee(monkeypatch):
    """Make `import marquee` raise ModuleNotFoundError regardless of install state."""
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "marquee" or name.startswith("marquee."):
            raise ModuleNotFoundError("No module named 'marquee'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_serve_errors_clearly_without_marquee(monkeypatch, tmp_path):
    _hide_marquee(monkeypatch)
    with pytest.raises(RuntimeError, match="marquee"):
        serve(tmp_path)


def test_parse_tunnel_url_errors_clearly_without_marquee(monkeypatch):
    _hide_marquee(monkeypatch)
    with pytest.raises(RuntimeError, match="marquee"):
        parse_tunnel_url("https://x.trycloudflare.com")


def test_serve_delegates_to_marquee(monkeypatch):
    """When marquee is importable, the shim forwards args and returns its result."""
    import types
    fake = types.ModuleType("marquee")
    calls = {}

    def fake_serve(directory, *, entry, port, provider, wait):
        calls.update(directory=directory, entry=entry, provider=provider)
        return "https://x.trycloudflare.com/status.html", lambda: None

    fake.serve = fake_serve
    fake.parse_tunnel_url = lambda text: "PARSED"
    monkeypatch.setitem(sys.modules, "marquee", fake)

    url, stop = serve("runs", provider="ngrok")
    assert url.endswith("/status.html") and calls["provider"] == "ngrok"
    assert parse_tunnel_url("anything") == "PARSED"
