"""Tests for ferry.cas (ported from cloudfs).

Unit tests use an in-memory fake GCS backend (no network). The integration
test hits real GCS and is skipped unless FERRY_CAS_TEST_BUCKET is set and ADC
is available.
"""

import hashlib
import os
import uuid

import pytest

from ferry.cas import Client


# --- in-memory fake GCS backend -------------------------------------------


class FakeBlob:
    def __init__(self, store, name):
        self._store = store
        self.name = name

    def exists(self):
        return self.name in self._store

    def upload_from_filename(self, path):
        with open(path, "rb") as f:
            self._store[self.name] = f.read()

    def upload_from_string(self, data):
        self._store[self.name] = data if isinstance(data, bytes) else data.encode()

    def download_to_filename(self, path):
        with open(path, "wb") as f:
            f.write(self._store[self.name])

    def download_as_bytes(self):
        return self._store[self.name]

    def delete(self):
        del self._store[self.name]


class FakeBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, name):
        return FakeBlob(self._store, name)


class FakeClient:
    def __init__(self):
        self.store = {}

    def bucket(self, name):
        return FakeBucket(self.store)


@pytest.fixture
def client():
    fake = FakeClient()
    return Client(bucket="test-bucket", prefix="p/q", client=fake)


# --- unit tests ------------------------------------------------------------


def test_upload_bytes_returns_md5(client):
    data = b"hello ferry.cas"
    file_id = client.upload_bytes(data)
    assert file_id == hashlib.md5(data).hexdigest()


def test_roundtrip_bytes(client):
    data = b"some content"
    file_id = client.upload_bytes(data)
    assert client.exists(file_id)
    assert client.download_bytes(file_id) == data


def test_roundtrip_file(client, tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"\x00\x01\x02file body")
    file_id = client.upload(src)
    assert file_id == hashlib.md5(src.read_bytes()).hexdigest()

    dest = tmp_path / "out" / "got.bin"
    client.download(file_id, dest)
    assert dest.read_bytes() == src.read_bytes()


def test_upload_is_idempotent_and_dedups(client):
    a = client.upload_bytes(b"dup")
    b = client.upload_bytes(b"dup")
    assert a == b
    assert len(client._client.store) == 1


def test_key_layout_uses_prefix_and_id(client):
    file_id = client.upload_bytes(b"x")
    assert client.uri(file_id) == f"gs://test-bucket/p/q/{file_id}"


def test_exists_false_for_unknown(client):
    assert client.exists("0" * 32) is False


def test_download_missing_raises(client):
    with pytest.raises(FileNotFoundError):
        client.download_bytes("0" * 32)


def test_delete(client):
    file_id = client.upload_bytes(b"to delete")
    assert client.delete(file_id) is True
    assert client.exists(file_id) is False
    assert client.delete(file_id) is False


def test_prefix_strip():
    c = Client(bucket="b", prefix="/lead/trail/", client=FakeClient())
    assert c.prefix == "lead/trail"


# --- integration test ------------------------------------------------------


@pytest.mark.integration
def test_real_gcs_roundtrip():
    bucket = os.environ.get("FERRY_CAS_TEST_BUCKET")
    if not bucket:
        pytest.skip("set FERRY_CAS_TEST_BUCKET to run the integration test")
    c = Client(bucket=bucket, prefix=f"ferry-cas-test/{uuid.uuid4().hex}")
    data = f"integration {uuid.uuid4()}".encode()
    file_id = c.upload_bytes(data)
    try:
        assert c.exists(file_id)
        assert c.download_bytes(file_id) == data
    finally:
        c.delete(file_id)
    assert not c.exists(file_id)
