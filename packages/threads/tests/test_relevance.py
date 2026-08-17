"""relevance: the isolated scoring function — monotonicity + config override."""

from __future__ import annotations

import math

from threads.config import Config, RelevanceConfig
from threads.relevance import ThreadStats, relevance


def test_monotone_increasing_in_sessions():
    cfg = Config()
    prev = -1.0
    for n in range(0, 20):
        score = relevance(ThreadStats(n, days_since_last_session=1.0), cfg)
        assert score > prev
        prev = score


def test_monotone_decreasing_in_days_since():
    cfg = Config()
    prev = float("inf")
    for d in range(0, 30):
        score = relevance(ThreadStats(5, days_since_last_session=float(d)), cfg)
        assert score < prev
        prev = score


def test_never_active_has_no_recency_term():
    cfg = Config()
    # days_since = None → only the sessions term
    s = relevance(ThreadStats(4, None), cfg)
    assert math.isclose(s, cfg.relevance.w_sessions * math.log1p(4))


def test_formula_matches_spec():
    cfg = Config()
    r = cfg.relevance
    got = relevance(ThreadStats(3, 2.0), cfg)
    want = r.w_sessions * math.log1p(3) + r.w_recency * math.exp(-2.0 / r.tau)
    assert math.isclose(got, want)


def test_config_override_changes_weighting():
    base = Config()
    # under the recency-tilted defaults, a fresh-but-thin thread beats a
    # stale-but-busy one
    hot = relevance(ThreadStats(1, 0.0), base)
    busy = relevance(ThreadStats(10, 20.0), base)
    assert hot > busy

    # crank the session weight → the busy thread overtakes; the ranking flips
    sessions_cfg = Config(relevance=RelevanceConfig(w_sessions=5.0, w_recency=1.0,
                                                    tau=7.0, window_days=30))
    hot2 = relevance(ThreadStats(1, 0.0), sessions_cfg)
    busy2 = relevance(ThreadStats(10, 20.0), sessions_cfg)
    assert busy2 > hot2


def test_tau_zero_disables_recency():
    cfg = Config(relevance=RelevanceConfig(tau=0.0))
    s = relevance(ThreadStats(2, 3.0), cfg)
    assert math.isclose(s, cfg.relevance.w_sessions * math.log1p(2))
