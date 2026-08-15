"""ferry — Pythonic push/pull between local and any rclone remote.

Two transports:
  - `ferry.push`/`ferry.pull`/`ferry.Remote` — move trees by *path* (rclone).
    Endpoints may be zero-config cloud URLs (``gs://…``, ``s3://…``, auth from
    the environment) or configured rclone remotes (``gcs:…``).
  - `ferry.cas` — store single files by *content hash* on GCS (absorbed from
    the retired `cloudfs` library; needs `pip install "ferry-sync[gcs]"`).
"""

from ferry import cas
from ferry.core import (
    Remote,
    RcloneError,
    RcloneNotFound,
    RcloneResult,
    ensure_rclone,
    listremotes,
    ls,
    pull,
    push,
    size,
)

__all__ = [
    "push",
    "pull",
    "ls",
    "size",
    "Remote",
    "ensure_rclone",
    "listremotes",
    "RcloneResult",
    "RcloneError",
    "RcloneNotFound",
    "cas",
]

__version__ = "0.3.0"
