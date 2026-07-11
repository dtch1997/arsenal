"""Wiki server + async client, exercised against a real local server instance."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from lobby.state import LobbyError
from lobby.wiki import Wiki
from lobby.wiki import httpd as wserver

TOKEN = "test-token-123"


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """A live wiki server on a free port + a Wiki handle pointed at it."""
    monkeypatch.setenv("WIKI_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WIKI_TOKEN", TOKEN)
    monkeypatch.setenv("LOBBY_STATE_DIR", str(tmp_path / "state"))
    srv = wserver.ThreadingHTTPServer(("127.0.0.1", 0), wserver.WikiHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield Wiki("test", url, TOKEN, pod_id="local")
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def tree(tmp_path):
    """A local content tree: markdown + a static site dir + a binary."""
    root = tmp_path / "tree"
    (root / "site").mkdir(parents=True)
    (root / "report.md").write_text("# Sleeper sweep\n\nSome **bold** claim.\n")
    (root / "site" / "index.html").write_text("<h1>hello</h1><img src=plot.png>")
    (root / "site" / "plot.png").write_bytes(b"\x89PNG fake")
    (root / "notes").mkdir()
    (root / "notes" / "a.md").write_text("# note a\n")
    return root


def _get(url: str, expect: int = 200) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        assert e.code == expect, f"{url}: got {e.code}, want {expect}"
        return e.code, e.read()


def test_ping_and_empty_index(wiki):
    code, body = _get(f"{wiki.url}/api/ping")
    info = json.loads(body)
    assert info["app"] == "lobby-wiki" and info["writable"] is True and info["files"] == 0
    code, body = _get(f"{wiki.url}/")
    assert code == 200 and b"Empty" in body


def test_writes_require_token(wiki):
    req = urllib.request.Request(f"{wiki.url}/api/state", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 401

    bad = Wiki("test", wiki.url, "wrong-token", pod_id="local")
    with pytest.raises(LobbyError, match="401"):
        asyncio.run(bad.push(Path(__file__).parent))


def test_push_serves_tree(wiki, tree):
    assert asyncio.run(wiki.push(tree)) == 4

    code, body = _get(f"{wiki.url}/")  # root listing
    assert b"report.md" in body and b"site" in body and b"notes" in body

    code, body = _get(f"{wiki.url}/report.md")  # rendered markdown
    assert b"Sleeper sweep" in body and b"marked" in body
    code, body = _get(f"{wiki.url}/report.md?raw")  # raw source
    assert body.startswith(b"# Sleeper sweep")

    code, body = _get(f"{wiki.url}/site/")  # dir with index.html -> served
    assert b"<h1>hello</h1>" in body
    code, body = _get(f"{wiki.url}/site/plot.png")
    assert body == b"\x89PNG fake"

    code, body = _get(f"{wiki.url}/notes/")  # dir without index -> listing
    assert b"a.md" in body


def test_pull_round_trip(wiki, tree, tmp_path):
    asyncio.run(wiki.push(tree))
    dest = asyncio.run(wiki.pull(tmp_path / "clone"))
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert files == ["notes/a.md", "report.md", "site/index.html", "site/plot.png"]
    assert (dest / "report.md").read_text() == (tree / "report.md").read_text()


def test_push_is_total_replacement(wiki, tree):
    asyncio.run(wiki.push(tree))
    (tree / "report.md").unlink()
    (tree / "new.md").write_text("# new\n")
    asyncio.run(wiki.push(tree))
    _get(f"{wiki.url}/report.md", expect=404)
    code, _ = _get(f"{wiki.url}/new.md")
    assert code == 200


def test_add_and_rm(wiki, tree, tmp_path):
    url = asyncio.run(wiki.add(tree / "report.md", name="my findings"))
    assert url == f"{wiki.url}/my-findings.md"
    assert _get(url)[0] == 200

    url = asyncio.run(wiki.add(tree / "site"))
    assert url == f"{wiki.url}/site/"
    assert b"<h1>hello</h1>" in _get(url)[1]

    assert asyncio.run(wiki.ls()) == ["my-findings.md", "site/"]

    asyncio.run(wiki.rm("site"))
    _get(f"{wiki.url}/site/", expect=404)
    assert asyncio.run(wiki.ls()) == ["my-findings.md"]


def test_hidden_and_traversal(wiki, tree, monkeypatch):
    asyncio.run(wiki.push(tree))
    data = Path(wserver.data_dir())
    (data / ".secret").write_text("hidden")
    _get(f"{wiki.url}/.secret", expect=404)
    _, body = _get(f"{wiki.url}/")
    assert b".secret" not in body

    # raw connection: urllib would normalize the .. away before sending
    conn = http.client.HTTPConnection("127.0.0.1", int(wiki.url.rsplit(":", 1)[1]))
    conn.request("GET", "/notes/../../etc/passwd")
    assert conn.getresponse().status == 404
    conn.close()

    # pulled state must not include dotfiles either
    dest = asyncio.run(wiki.pull())
    assert not (dest / ".secret").exists()
