"""arXiv identifier normalization.

Accepts bare IDs (new ``2401.12345`` / old ``hep-th/9901001`` style, with or
without a ``vN`` suffix) and any common arxiv.org URL form (abs / pdf / html,
plus ar5iv mirrors), and normalizes to ``(id, version | None)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# New-style: YYMM.NNNNN (4 or 5 digits after the dot, 2007-present)
_NEW_ID = r"\d{4}\.\d{4,5}"
# Old-style: archive[.subclass]/YYMMNNN, e.g. hep-th/9901001, math.GT/0309136
_OLD_ID = r"[a-z-]+(?:\.[A-Za-z-]+)?/\d{7}"
_ID_RE = re.compile(rf"(?P<id>{_NEW_ID}|{_OLD_ID})(?:v(?P<version>\d+))?")

_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:ar5iv\.labs\.)?arxiv\.org/"
    r"(?:abs|pdf|html|format|e-print)/(?P<rest>.+?)(?:\.pdf)?/?$"
)


class InvalidArxivId(ValueError):
    """Raised when a string cannot be interpreted as an arXiv identifier."""


@dataclass(frozen=True)
class ArxivId:
    id: str
    version: int | None = None

    @property
    def versioned(self) -> str:
        """``2401.12345v2`` if the version is known, else the bare id."""
        return f"{self.id}v{self.version}" if self.version else self.id

    @property
    def slug(self) -> str:
        """Filesystem-safe form (old-style ids contain a slash)."""
        return self.versioned.replace("/", "_")

    def __str__(self) -> str:
        return self.versioned


def parse_arxiv_id(raw: str) -> ArxivId:
    """Parse a bare id, versioned id, or arxiv.org URL into an :class:`ArxivId`."""
    s = raw.strip()
    m = _URL_RE.match(s)
    if m:
        s = m.group("rest")
    m = _ID_RE.fullmatch(s)
    if not m:
        raise InvalidArxivId(f"not a recognizable arXiv id or URL: {raw!r}")
    version = m.group("version")
    return ArxivId(id=m.group("id"), version=int(version) if version else None)
