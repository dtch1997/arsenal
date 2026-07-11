"""habitat: a personal habit tracker on a long-lived RunPod CPU pod.

Celebration-first: it tracks what you *did* (recency, monthly counts, a
heatmap to admire), not a guilt list of daily obligations. See README.md.
"""

from .client import (  # noqa: F401
    HabitatError,
    backup,
    load_config,
    ping,
    provision,
    push_code,
    restore,
    seed,
)
