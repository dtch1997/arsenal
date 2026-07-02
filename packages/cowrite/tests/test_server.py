"""End-to-end tests for the editor server's save/sync round-trip."""

import json
import threading
import urllib.request
import urllib.error

import pytest
from http.server import ThreadingHTTPServer

from cowrite.server import content_rev, make_handler


@pytest.fixture()
def editor(tmp_path):
    """A live server on a random port editing a temp draft; yields (url, draft)."""
    draft = tmp_path / "draft.md"
    draft.write_text("# Title\n\noriginal\n", encoding="utf-8")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(draft, "test"))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", draft
    httpd.shutdown()


def req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        resp = urllib.request.urlopen(r)
    except urllib.error.HTTPError as e:
        resp = e
    return resp.status, resp.read()


def test_page_and_state_carry_disk_rev(editor):
    url, draft = editor
    disk_rev = content_rev(draft.read_text())
    status, page = req(url + "/")
    assert status == 200
    assert f"let rev = '{disk_rev}';".encode() in page
    status, body = req(url + "/api/state")
    assert (status, json.loads(body)["rev"]) == (200, disk_rev)


def test_save_with_current_rev_writes(editor):
    url, draft = editor
    base = content_rev(draft.read_text())
    status, body = req(url + "/save", "POST", b"# Title\n\nedited\n",
                       {"X-Base-Rev": base})
    data = json.loads(body)
    assert (status, data["ok"]) == (200, True)
    assert draft.read_text() == "# Title\n\nedited\n"
    assert data["rev"] == content_rev("# Title\n\nedited\n")


def test_save_over_external_change_conflicts(editor):
    url, draft = editor
    base = content_rev(draft.read_text())
    draft.write_text("# Title\n\nthe AI wrote this meanwhile\n", encoding="utf-8")
    status, body = req(url + "/save", "POST", b"# Title\n\nhuman edit\n",
                       {"X-Base-Rev": base})
    data = json.loads(body)
    assert (status, data["conflict"]) == (409, True)
    assert data["disk"] == "# Title\n\nthe AI wrote this meanwhile\n"
    # the AI's version survives the refused save
    assert draft.read_text() == "# Title\n\nthe AI wrote this meanwhile\n"
    # retrying with the rev from the 409 (the user confirmed overwrite) wins
    status, body = req(url + "/save", "POST", b"# Title\n\nhuman edit\n",
                       {"X-Base-Rev": data["rev"]})
    assert (status, json.loads(body)["ok"]) == (200, True)
    assert draft.read_text() == "# Title\n\nhuman edit\n"


def test_save_without_base_rev_still_writes(editor):
    # old clients / plain curl keep working
    url, draft = editor
    status, body = req(url + "/save", "POST", b"no header\n")
    assert (status, json.loads(body)["ok"]) == (200, True)
    assert draft.read_text() == "no header\n"


def test_api_doc_returns_full_document(editor):
    url, draft = editor
    status, body = req(url + "/api/doc")
    data = json.loads(body)
    assert status == 200
    assert data["md"] == draft.read_text()
    assert data["rev"] == content_rev(data["md"])
    assert "<h1" in data["html"]
