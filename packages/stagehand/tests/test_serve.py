"""Unit tests for the serve helper. The cloudflared/http.server integration isn't
exercised here (no binary in CI); we cover the URL parser and the missing-binary
guard, which are the bits that fail confusingly otherwise."""
import pytest

import shutil

from stagehand.serve import parse_tunnel_url, serve


def test_parse_tunnel_url_extracts_from_log():
    log = ("2026-01-01 INF Starting tunnel\n"
           "2026-01-01 INF |  https://brave-cloud-1234.trycloudflare.com  |\n"
           "2026-01-01 INF Connection registered\n")
    assert parse_tunnel_url(log) == "https://brave-cloud-1234.trycloudflare.com"


def test_parse_tunnel_url_none_when_absent():
    assert parse_tunnel_url("no url here yet") is None


def test_serve_errors_clearly_without_cloudflared(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="cloudflared not found"):
        serve(tmp_path)
