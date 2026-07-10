"""ferry — Pythonic push/pull between local and any rclone remote.

Two transports:
  - `ferry.push`/`ferry.pull`/`ferry.Remote` — move trees by *path* (rclone).
  - `ferry.cas` — store single files by *content hash* on GCS (absorbed from
    the retired `cloudfs` library; needs `pip install "ferry-sync[gcs]"`).
"""

from ferry import cas
from ferry.core import (
    Remote,
    RcloneError,
    RcloneNotFound,
    RcloneResult,
    listremotes,
    pull,
    push,
)

__all__ = [
    "push",
    "pull",
    "Remote",
    "listremotes",
    "RcloneResult",
    "RcloneError",
    "RcloneNotFound",
    "cas",
]

__version__ = "0.2.0"
