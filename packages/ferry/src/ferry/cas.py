"""Content-addressed file store on Google Cloud Storage (absorbed from `cloudfs`).

Files are keyed by the MD5 hex digest of their content, so uploads are
idempotent and identical content is stored once. The original filename is
not retained.

This is ferry's second transport: `ferry.core` moves *trees* by path (rclone),
`ferry.cas` stores *files* by content hash (google-cloud-storage, installed via
`pip install "ferry-sync[gcs]"`). The heavy import happens only when a real
client is constructed, so `import ferry` stays dependency-free.

Bucket/prefix/project resolve from FERRY_CAS_BUCKET / FERRY_CAS_PREFIX /
FERRY_CAS_PROJECT, falling back to the legacy CLOUDFS_* variables and then to
the package defaults (unchanged from cloudfs, so existing stored objects keep
resolving).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional, Union

DEFAULT_BUCKET = "alignment-team-general-storage"
DEFAULT_PREFIX = "daniel/cloudfs"  # unchanged from cloudfs: existing ids must keep resolving

# Pure object operations (get/put/delete) are billed to the bucket's project,
# not the client's, so when ADC supplies no project we fall back to this
# cosmetic placeholder rather than failing to construct the client.
_FALLBACK_PROJECT = "ferry-cas"

_CHUNK = 1024 * 1024  # 1 MiB streaming chunk

PathLike = Union[str, os.PathLike]


def _env(name: str) -> Optional[str]:
    return os.environ.get(f"FERRY_CAS_{name}") or os.environ.get(f"CLOUDFS_{name}")


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class Client:
    """A handle to a content-addressed store backed by one GCS bucket/prefix.

    Auth uses Application Default Credentials. Bucket and prefix fall back to
    the ``FERRY_CAS_BUCKET`` / ``FERRY_CAS_PREFIX`` (or legacy ``CLOUDFS_*``)
    environment variables and then to the package defaults.
    """

    def __init__(
        self,
        bucket: Optional[str] = None,
        prefix: Optional[str] = None,
        *,
        project: Optional[str] = None,
        client=None,
    ) -> None:
        self.bucket_name = bucket or _env("BUCKET") or DEFAULT_BUCKET
        prefix = prefix if prefix is not None else (_env("PREFIX") or DEFAULT_PREFIX)
        self.prefix = prefix.strip("/")
        self._client = client or self._make_client(project)
        self._bucket = self._client.bucket(self.bucket_name)

    @staticmethod
    def _make_client(project: Optional[str]):
        try:
            from google.cloud import storage  # lazy: only a real client needs it
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "ferry.cas needs google-cloud-storage — install it with "
                '`pip install "ferry-sync[gcs]"`.'
            ) from e
        project = project or _env("PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            return storage.Client(project=project)
        try:
            # Let ADC determine the project when it can.
            return storage.Client()
        except OSError:
            # ADC supplied no project; object ops don't need a real one.
            return storage.Client(project=_FALLBACK_PROJECT)

    def _blob(self, file_id: str):
        key = f"{self.prefix}/{file_id}" if self.prefix else file_id
        return self._bucket.blob(key)

    def uri(self, file_id: str) -> str:
        """Return the ``gs://`` URI for a file id."""
        return f"gs://{self.bucket_name}/{self._blob(file_id).name}"

    def exists(self, file_id: str) -> bool:
        return self._blob(file_id).exists()

    def upload(self, path: PathLike) -> str:
        """Upload a file. Returns its id (MD5 hex digest). Idempotent."""
        path = Path(path)
        file_id = _md5_file(path)
        blob = self._blob(file_id)
        if not blob.exists():
            blob.upload_from_filename(str(path))
        return file_id

    def upload_bytes(self, data: bytes) -> str:
        """Upload raw bytes. Returns its id (MD5 hex digest). Idempotent."""
        file_id = _md5_bytes(data)
        blob = self._blob(file_id)
        if not blob.exists():
            blob.upload_from_string(data)
        return file_id

    def download(self, file_id: str, dest: PathLike) -> Path:
        """Download a file by id to ``dest``. Returns the destination path."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob = self._blob(file_id)
        if not blob.exists():
            raise FileNotFoundError(f"no object for id {file_id} at {self.uri(file_id)}")
        blob.download_to_filename(str(dest))
        return dest

    def download_bytes(self, file_id: str) -> bytes:
        """Download a file by id and return its bytes."""
        blob = self._blob(file_id)
        if not blob.exists():
            raise FileNotFoundError(f"no object for id {file_id} at {self.uri(file_id)}")
        return blob.download_as_bytes()

    def delete(self, file_id: str) -> bool:
        """Delete a file by id. Returns True if it existed, False otherwise."""
        blob = self._blob(file_id)
        if not blob.exists():
            return False
        blob.delete()
        return True


_default: Optional[Client] = None


def default_client() -> Client:
    """Return a lazily-constructed process-wide default client."""
    global _default
    if _default is None:
        _default = Client()
    return _default


def upload(path: PathLike) -> str:
    return default_client().upload(path)


def upload_bytes(data: bytes) -> str:
    return default_client().upload_bytes(data)


def download(file_id: str, dest: PathLike) -> Path:
    return default_client().download(file_id, dest)


def download_bytes(file_id: str) -> bytes:
    return default_client().download_bytes(file_id)


def exists(file_id: str) -> bool:
    return default_client().exists(file_id)


def delete(file_id: str) -> bool:
    return default_client().delete(file_id)


def uri(file_id: str) -> str:
    return default_client().uri(file_id)
