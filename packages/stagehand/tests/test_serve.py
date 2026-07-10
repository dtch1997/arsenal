"""Unit tests for the serve shim. The implementation lives in the `lobby`
library (tested there); here we cover the lazy wrapper: a clear error when
lobby is absent, and that calls delegate to it when present."""
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


def test_serve_errors_clearly_without_lobby(monkeypatch, tmp_path):
    _hide(monkeypatch, "lobby")
    with pytest.raises(RuntimeError, match="lobby"):
        serve(tmp_path)


def test_parse_tunnel_url_errors_clearly_without_lobby(monkeypatch):
    _hide(monkeypatch, "lobby")
    with pytest.raises(RuntimeError, match="lobby"):
        parse_tunnel_url("https://x.trycloudflare.com")


def test_serve_delegates_to_lobby(monkeypatch, tmp_path):
    calls = {}
    _fake_lobby(monkeypatch, calls)
    runs = tmp_path / "runs"
    runs.mkdir()

    url, stop = serve(runs, name="sweep", title="Sleeper sweep")
    assert url == "https://hub.example/a/sweep/status.html"
    assert calls["kind"] == "stagehand" and calls["title"] == "Sleeper sweep"
    assert calls["directory"] == str(runs) and callable(stop)


def test_serve_name_defaults_to_directory_name(monkeypatch, tmp_path):
    calls = {}
    _fake_lobby(monkeypatch, calls)
    runs = tmp_path / "my-flow-runs"
    runs.mkdir()
    serve(runs)
    assert calls["name"] == "my-flow-runs"


def test_parse_tunnel_url_delegates(monkeypatch):
    fake = types.ModuleType("lobby")
    fake.parse_tunnel_url = lambda text: "PARSED"
    monkeypatch.setitem(sys.modules, "lobby", fake)
    assert parse_tunnel_url("anything") == "PARSED"
