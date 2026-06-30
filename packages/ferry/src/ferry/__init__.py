"""ferry — Pythonic push/pull between local and any rclone remote."""

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
]

__version__ = "0.1.0"
