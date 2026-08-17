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


# ---- anchored comments -------------------------------------------------------

def test_page_author_from_query(editor):
    url, _ = editor
    _, page = req(url + "/")
    assert b"let COWRITE_AUTHOR = 'daniel';" in page
    _, page = req(url + "/?author=alice")
    assert b"let COWRITE_AUTHOR = 'alice';" in page


def test_save_of_a_comment_marker_persists_and_is_parsed(editor):
    # inserting a comment is an ordinary save of source that contains a marker
    url, draft = editor
    base = content_rev(draft.read_text())
    body_md = "# Title\n\noriginal <!-- cowrite[daniel]: tighten -->\n"
    status, body = req(url + "/save", "POST", body_md.encode(), {"X-Base-Rev": base})
    data = json.loads(body)
    assert (status, data["ok"]) == (200, True)
    # the marker lands on disk verbatim (the AI sees it on its next re-read)...
    assert "<!-- cowrite[daniel]: tighten -->" in draft.read_text()
    # ...and the save response carries the parsed bubble, not raw marker text
    assert data["comments"][0]["text"] == "tighten"
    assert data["comments"][0]["block"] == 1  # anchored to the paragraph
    assert "cowrite" not in data["html"]


def test_api_doc_carries_comments(editor):
    url, draft = editor
    draft.write_text("A. <!-- cowrite[bob]: q -->\n", encoding="utf-8")
    _, body = req(url + "/api/doc")
    data = json.loads(body)
    assert data["comments"][0]["author"] == "bob"
    assert data["comments"][0]["text"] == "q"
