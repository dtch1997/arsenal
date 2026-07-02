"""cairn — a minimal, git-friendly issue graph for coding agents.

Trail-marker stones for long-horizon work: a dependency-aware issue tracker
with a `ready` queue and persistent agent memory, stored as one small JSON
file per issue so concurrent agents and branches merge cleanly. Stdlib only.
"""

from .models import Issue, Status, gen_id
from .store import Store, CairnError, find_root

__version__ = "0.1.0"

__all__ = ["Issue", "Status", "gen_id", "Store", "CairnError", "find_root", "__version__"]
