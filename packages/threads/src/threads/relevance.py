"""The thread relevance score — isolated so Daniel can refine the formula.

``relevance(stats, cfg)`` is a pure function of a small stats struct and the
``[relevance]`` config block. Daniel explicitly expects to iterate on this over
time, so it lives here alone with its own tests; everything else (sorting,
roll-ups, the dashboard) calls it and never re-derives the arithmetic.

    relevance = w_sessions * log1p(sessions_in_window)
              + w_recency  * exp(-days_since_last_session / tau)

Monotone increasing in ``sessions_in_window`` and decreasing in
``days_since_last_session`` for the default (non-negative) weights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config, RelevanceConfig


@dataclass(frozen=True)
class ThreadStats:
    """The minimal inputs the relevance formula reads. ``days_since_last_session``
    is ``None`` for a node that has never been active (recency term → 0)."""

    sessions_in_window: int
    days_since_last_session: float | None


def relevance(stats: ThreadStats, cfg: Config | RelevanceConfig) -> float:
    rel = cfg.relevance if isinstance(cfg, Config) else cfg
    n = max(0, stats.sessions_in_window)
    sessions_term = rel.w_sessions * math.log1p(n)

    d = stats.days_since_last_session
    if d is None or rel.tau <= 0:
        recency_term = 0.0
    else:
        recency_term = rel.w_recency * math.exp(-max(0.0, d) / rel.tau)
    return sessions_term + recency_term
