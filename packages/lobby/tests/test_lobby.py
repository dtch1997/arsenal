from __future__ import annotations

import http.client
import json

import lobby
from lobby import client, state


def fetch(hub_port: int, path: str, method: str = "GET", body: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", hub_port, timeout=10)
    conn.request(method, path, body=body)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_ping_and_hub_url(hub):
    assert hub["info"]["app"] == "lobby"
    assert hub["info"]["ready"] is True
    assert lobby.hub_url() == f"http://127.0.0.1:{hub['port']}"


def test_second_ensure_hub_reuses_daemon(hub):
    info = client.ensure_hub(tunnel=False, wait=5)
    assert info["pid"] == hub["info"]["pid"]


def test_register_and_proxy_roundtrip(hub, backend):
    port, _ = backend()
    url = lobby.serve(port, name="Echo App!", kind="test", title="round trip")
    assert url.endswith("/a/echo-app/")  # slugified

    resp, data = fetch(hub["port"], "/a/echo-app/some/path?x=1&y=2")
    assert resp.status == 200
    assert resp.getheader("X-Backend") == "echo"
    echoed = json.loads(data)
    assert echoed["method"] == "GET"
    assert echoed["path"] == "/some/path?x=1&y=2"  # prefix stripped, query kept

    resp, data = fetch(hub["port"], "/a/echo-app/save", method="POST", body="hello=1")
    echoed = json.loads(data)
    assert echoed == {"method": "POST", "path": "/save", "body": "hello=1"}


def test_index_lists_apps(hub, backend):
    port, _ = backend()
    lobby.serve(port, name="indexed", kind="test", title="shows up")
    resp, data = fetch(hub["port"], "/")
    page = data.decode()
    assert resp.status == 200
    assert 'href="/a/indexed/"' in page
    assert "shows up" in page


def test_missing_trailing_slash_redirects(hub, backend):
    port, _ = backend()
    lobby.serve(port, name="slashless", kind="test")
    resp, _ = fetch(hub["port"], "/a/slashless?q=1")
    assert resp.status == 301
    assert resp.getheader("Location") == "/a/slashless/?q=1"


def test_backend_redirect_rewritten_into_mount(hub, backend):
    port, _ = backend()
    lobby.serve(port, name="redirector", kind="test")
    resp, _ = fetch(hub["port"], "/a/redirector/redirect")
    assert resp.status == 302
    assert resp.getheader("Location") == "/a/redirector/landed"


def test_name_collision_gets_suffix(hub, backend):
    port1, _ = backend()
    port2, _ = backend()
    url1 = lobby.serve(port1, name="twin", kind="test")
    url2 = lobby.serve(port2, name="twin", kind="test")
    assert url1.endswith("/a/twin/")
    assert url2.endswith("/a/twin-2/")


def test_reregister_same_port_keeps_name(hub, backend):
    port, _ = backend()
    url1 = lobby.serve(port, name="sticky", kind="test")
    url2 = lobby.serve(port, name="sticky", kind="test", title="updated")
    assert url1 == url2
    apps = {a["name"]: a for a in state.list_apps()}
    assert apps["sticky"]["title"] == "updated"


def test_dead_app_shows_ended_and_502(hub, backend):
    port, srv = backend()
    lobby.serve(port, name="doomed", kind="test", pid=None)
    srv.shutdown()
    srv.server_close()

    resp, data = fetch(hub["port"], "/a/doomed/")
    assert resp.status == 502
    _, data = fetch(hub["port"], "/")
    assert "Ended" in data.decode()


def test_unknown_app_404(hub):
    resp, _ = fetch(hub["port"], "/a/never-registered/")
    assert resp.status == 404


def test_serve_dir(hub, tmp_path):
    (tmp_path / "status.html").write_text("<h1>flow live</h1>")
    url, stop = lobby.serve_dir(str(tmp_path), name="flowdash", kind="stagehand",
                                entry="status.html")
    assert url.endswith("/a/flowdash/status.html")
    resp, data = fetch(hub["port"], "/a/flowdash/status.html")
    assert resp.status == 200
    assert b"flow live" in data
    stop()
    resp, _ = fetch(hub["port"], "/a/flowdash/status.html")
    assert resp.status == 502


def test_unregister(hub, backend):
    port, _ = backend()
    lobby.serve(port, name="transient", kind="test")
    lobby.unregister("transient")
    resp, _ = fetch(hub["port"], "/a/transient/")
    assert resp.status == 404
