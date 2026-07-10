"""cairn — a minimal, git-friendly issue graph for coding agents.

Trail-marker stones for long-horizon work: a dependency-aware issue tracker
with a `ready` queue and persistent agent memory, stored as one small JSON
file per issue so concurrent agents and branches merge cleanly. Stdlib only.
"""

from .models import Issue, Status, gen_id
from .store import Store, CairnError, find_root
from .beads import import_beads, beads_record_to_issue, ImportResult

__version__ = "0.2.0"

__all__ = [
    "Issue",
    "Status",
    "gen_id",
    "Store",
    "CairnError",
    "find_root",
    "import_beads",
    "beads_record_to_issue",
    "ImportResult",
    "__version__",
]
