"""dashboard view: sort keys, filters, query-param round-trip, config.toml."""

from __future__ import annotations

from datetime import timedelta

from threads import config, dashboard
from threads.dashboard import Thread, ViewParams, apply_view, params_from_query

from conftest import NOW


def _t(slug, *, rel, sessions, days_ago, dormant=False, title=""):
    last = NOW - timedelta(days=days_ago)
    recs = [{"title": title if i == 0 else ""} for i in range(sessions)]
    return Thread(slug=slug, sessions=recs, last_activity=last,
                  dormant=dormant, closed=False, status_line="",
                  relevance=rel, sessions_in_window=sessions,
                  days_since_last=float(days_ago))


THREADS = [
    _t("alpha", rel=1.0, sessions=2, days_ago=1, title="fixing arch2 bug"),
    _t("bravo", rel=5.0, sessions=10, days_ago=0, title="dogfight rl launch"),
    _t("charlie", rel=0.2, sessions=1, days_ago=40, dormant=True, title="old"),
]


def test_sort_relevance_default():
    out = apply_view(THREADS, ViewParams(sort="relevance"), NOW)
    assert [t.slug for t in out] == ["bravo", "alpha", "charlie"]


def test_sort_name():
    out = apply_view(THREADS, ViewParams(sort="name"), NOW)
    assert [t.slug for t in out] == ["alpha", "bravo", "charlie"]


def test_sort_sessions_and_last_activity():
    out = apply_view(THREADS, ViewParams(sort="sessions"), NOW)
    assert out[0].slug == "bravo"
    out = apply_view(THREADS, ViewParams(sort="last-activity"), NOW)
    assert out[0].slug == "bravo" and out[-1].slug == "charlie"


def test_filter_active_days():
    out = apply_view(THREADS, ViewParams(filter_active_days=7), NOW)
    assert {t.slug for t in out} == {"alpha", "bravo"}


def test_filter_dormant_only():
    out = apply_view(THREADS, ViewParams(dormant_only=True), NOW)
    assert [t.slug for t in out] == ["charlie"]


def test_text_search_over_slug_and_title():
    out = apply_view(THREADS, ViewParams(q="dogfight"), NOW)
    assert [t.slug for t in out] == ["bravo"]
    out = apply_view(THREADS, ViewParams(q="alpha"), NOW)
    assert [t.slug for t in out] == ["alpha"]


def test_params_from_query_roundtrip():
    p = ViewParams(sort="sessions", filter_active_days=14, dormant_only=True,
                   q="arch2")
    parsed = params_from_query(
        {k: [v] for k, v in
         [x.split("=") for x in p.query_string().split("&")]})
    assert parsed.sort == "sessions"
    assert parsed.filter_active_days == 14
    assert parsed.dormant_only is True
    assert parsed.q == "arch2"


def test_params_from_query_defaults_and_bad_input():
    p = params_from_query({})
    assert p.sort == "relevance" and p.filter_active_days is None
    p = params_from_query({"sort": ["nonsense"], "active_days": ["oops"]})
    assert p.sort == "relevance"  # invalid falls back
    assert p.filter_active_days is None


def test_config_toml_override(env):
    config.ensure_spool()
    config.config_path().write_text(
        "[relevance]\nw_sessions = 3.0\nw_recency = 0.0\ntau = 5.0\n"
        "window_days = 10\n\n[dormancy]\ndays = 21\n\n[clustering]\nmin_siblings = 5\n")
    cfg = config.load_config()
    assert cfg.relevance.w_sessions == 3.0
    assert cfg.relevance.w_recency == 0.0
    assert cfg.relevance.window_days == 10
    assert cfg.dormant_days == 21
    assert cfg.cluster_min_siblings == 5


def test_config_defaults_when_missing(env):
    # ensure_spool writes a default config.toml; loading it yields the defaults
    config.ensure_spool()
    cfg = config.load_config()
    assert cfg.relevance.w_sessions == 1.0
    assert cfg.relevance.w_recency == 2.0
    assert cfg.relevance.tau == 7.0
