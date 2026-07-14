from __future__ import annotations

import json

import lobby
from lobby import cli, state

from test_lobby import fetch


def test_status_json(hub, backend, capsys):
    port, _ = backend()
    lobby.serve(port, name="status-target", kind="test", title="t")
    assert cli.main(["status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hub"]["pid"] == hub["info"]["pid"]
    app = next(a for a in out["apps"] if a["name"] == "status-target")
    assert app["live"] is True
    assert app["url"] == f"{hub['info']['url']}/a/status-target/"


def test_status_human_shows_urls(hub, backend, capsys):
    port, _ = backend()
    lobby.serve(port, name="human-status", kind="test", title="Nice Title")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "human-status" in out
    assert "/a/human-status/" in out  # full app URL, the point of the new status
    assert "Nice Title" in out


def test_url_command(hub, backend, capsys):
    port, _ = backend()
    lobby.serve(port, name="urly", kind="test")
    assert cli.main(["url"]) == 0
    assert cli.main(["url", "urly"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == hub["info"]["url"]
    assert lines[1] == f"{hub['info']['url']}/a/urly/"


def test_url_unknown_app_fails(hub):
    assert cli.main(["url", "never-registered-here"]) == 1


def test_cli_serve_port(hub, backend, capsys):
    port, _ = backend()
    assert cli.main(["serve", str(port), "--name", "cli-served", "--kind", "test"]) == 0
    url = capsys.readouterr().out.strip()
    assert url.endswith("/a/cli-served/")
    app = state.read_json(state.app_path("cli-served"))
    assert app["pid"] == 0  # no pid tracking: the CLI process is gone
    assert state.app_live(app)  # liveness is the TCP probe alone


def test_cli_serve_dir(hub, tmp_path, capsys):
    (tmp_path / "index.html").write_text("<h1>from the cli</h1>")
    assert cli.main(["serve", str(tmp_path), "--name", "cli-dir"]) == 0
    url = capsys.readouterr().out.strip()
    assert url.endswith("/a/cli-dir/")
    resp, data = fetch(hub["port"], "/a/cli-dir/index.html")
    assert resp.status == 200
    assert b"from the cli" in data
    assert cli.main(["stop", "cli-dir"]) == 0  # kill the detached file server


def test_cli_serve_bad_target(hub, capsys):
    assert cli.main(["serve", "not-a-port"]) == 1


def test_logs(hub, capsys):
    assert cli.main(["logs"]) == 0
    assert "lobby: hub on" in capsys.readouterr().out


def test_serving_context_manager(hub, backend):
    port, _ = backend()
    with lobby.serving(port, name="ctx-managed", kind="test") as url:
        assert url.endswith("/a/ctx-managed/")
        assert state.read_json(state.app_path("ctx-managed")) is not None
    assert state.read_json(state.app_path("ctx-managed")) is None
