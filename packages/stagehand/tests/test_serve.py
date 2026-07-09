"""Unit tests for the serve shim. The implementations live in the `lobby` and
`marquee` libraries (tested there); here we cover backend selection: lobby
preferred, marquee fallback, forced modes, and clear errors when absent."""
import builtins
import sys
import types

import pytest

from stagehand.serve import serve, parse_tunnel_url


def _hide(monkeypatch, *names):
    """Make `import <name>` raise ModuleNotFoundError regardless of install state."""
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if any(name == n or name.startswith(n + ".") for n in names):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for n in names:
        monkeypatch.delitem(sys.modules, n, raising=False)


def _fake_lobby(monkeypatch, calls):
    fake = types.ModuleType("lobby")

    def serve_dir(directory, *, name, kind, title, entry, port):
        calls.update(directory=directory, name=name, kind=kind, title=title,
                     entry=entry, port=port)
        return f"https://hub.example/a/{name}/{entry}", lambda: None

    fake.serve_dir = serve_dir
    monkeypatch.setitem(sys.modules, "lobby", fake)


def _fake_marquee(monkeypatch, calls):
    fake = types.ModuleType("marquee")

    def fake_serve(directory, *, entry, port, provider, wait):
        calls.update(directory=directory, entry=entry, provider=provider)
        return "https://x.trycloudflare.com/status.html", lambda: None

    fake.serve = fake_serve
    fake.parse_tunnel_url = lambda text: "PARSED"
    monkeypatch.setitem(sys.modules, "marquee", fake)


def test_serve_errors_clearly_without_either_backend(monkeypatch, tmp_path):
    _hide(monkeypatch, "lobby", "marquee")
    with pytest.raises(RuntimeError, match="lobby.*marquee|marquee.*lobby"):
        serve(tmp_path)


def test_parse_tunnel_url_errors_clearly_without_marquee(monkeypatch):
    _hide(monkeypatch, "marquee")
    with pytest.raises(RuntimeError, match="marquee"):
        parse_tunnel_url("https://x.trycloudflare.com")


def test_serve_prefers_lobby_hub(monkeypatch, tmp_path):
    calls = {}
    _fake_lobby(monkeypatch, calls)
    runs = tmp_path / "runs"
    runs.mkdir()

    url, stop = serve(runs, name="sweep", title="Sleeper sweep")
    assert url == "https://hub.example/a/sweep/status.html"
    assert calls["kind"] == "stagehand" and calls["title"] == "Sleeper sweep"
    assert calls["directory"] == str(runs)


def test_serve_hub_name_defaults_to_directory_name(monkeypatch, tmp_path):
    calls = {}
    _fake_lobby(monkeypatch, calls)
    runs = tmp_path / "my-flow-runs"
    runs.mkdir()
    serve(runs)
    assert calls["name"] == "my-flow-runs"


def test_serve_falls_back_to_marquee_without_lobby(monkeypatch):
    calls = {}
    _hide(monkeypatch, "lobby")
    _fake_marquee(monkeypatch, calls)

    url, stop = serve("runs", provider="ngrok")
    assert url.endswith("/status.html") and calls["provider"] == "ngrok"


def test_serve_hub_false_forces_marquee(monkeypatch):
    lobby_calls, marquee_calls = {}, {}
    _fake_lobby(monkeypatch, lobby_calls)
    _fake_marquee(monkeypatch, marquee_calls)

    url, _ = serve("runs", hub=False)
    assert not lobby_calls and marquee_calls["directory"] == "runs"


def test_serve_hub_true_requires_lobby(monkeypatch, tmp_path):
    _hide(monkeypatch, "lobby")
    with pytest.raises(RuntimeError, match="lobby"):
        serve(tmp_path, hub=True)


def test_parse_tunnel_url_delegates(monkeypatch):
    _fake_marquee(monkeypatch, {})
    assert parse_tunnel_url("anything") == "PARSED"
