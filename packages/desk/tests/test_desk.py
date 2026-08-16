"""desk tests — no network, no real `gh`. The gh subprocess is replaced by an
injected runner; flare.send is monkeypatched for the sync test."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import flare
from desk import core
from desk.collectors import (
    collect_concierge,
    collect_flares,
    collect_markers,
    collect_prs,
)
from desk.config import Config, load_config

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_HOME", str(tmp_path / "deskhome"))
    monkeypatch.setenv("DESK_CONFIG", str(tmp_path / "desk.toml"))
    monkeypatch.setenv("FLARE_HOME", str(tmp_path / "flarehome"))
    monkeypatch.delenv("FLARE_WEBHOOK", raising=False)
    return tmp_path


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_config_created_on_first_run_with_commented_defaults(tmp_path):
    cfg = load_config()
    assert cfg.github_repos == ["ArcadiaImpact/jarvis", "dtch1997/arsenal"]
    assert cfg.pr_age_warn_days == 7
    path = tmp_path / "desk.toml"
    assert path.exists()
    text = path.read_text()
    assert "# github_repos" in text and "# pr_age_warn_days = 7" in text


def test_config_overrides_read(tmp_path):
    (tmp_path / "desk.toml").write_text(
        'github_repos = ["me/repo"]\npr_age_warn_days = 3\n'
    )
    cfg = load_config()
    assert cfg.github_repos == ["me/repo"]
    assert cfg.pr_age_warn_days == 3


# --------------------------------------------------------------------------- #
# concierge collector
# --------------------------------------------------------------------------- #
def _tasks_dir(tmp_path):
    d = tmp_path / "ch" / "tasks"
    d.mkdir(parents=True)
    return d


def test_concierge_selects_blocked_and_failed_skips_wait_sidecar(tmp_path):
    tasks = _tasks_dir(tmp_path)
    (tasks / "t-1.json").write_text(json.dumps(
        {"id": "t-1", "title": "blocked one", "status": "blocked",
         "blocked_note": "need a decision", "updated_at": _iso(2)}))
    (tasks / "t-2.json").write_text(json.dumps(
        {"id": "t-2", "title": "failed one", "status": "failed",
         "error": "boom", "updated_at": _iso(5)}))
    (tasks / "t-3.json").write_text(json.dumps(
        {"id": "t-3", "title": "running", "status": "in_progress"}))
    (tasks / "t-1.wait.json").write_text(json.dumps(
        {"id": "t-1", "status": "blocked"}))  # sidecar — must be skipped

    cfg = Config(concierge_home=str(tmp_path / "ch"))
    items, warnings = collect_concierge(cfg, NOW)

    ids = {i["id"] for i in items}
    assert ids == {"concierge:t-1", "concierge:t-2"}
    assert warnings == []
    blocked = next(i for i in items if i["id"] == "concierge:t-1")
    assert blocked["title"] == "blocked one"
    assert "need a decision" in blocked["detail"]
    assert blocked["age_days"] == pytest.approx(2, abs=0.01)


def test_concierge_missing_dir_warns_not_crash(tmp_path):
    cfg = Config(concierge_home=str(tmp_path / "nope"))
    items, warnings = collect_concierge(cfg, NOW)
    assert items == []
    assert warnings and "not found" in warnings[0]


def test_concierge_malformed_json_warns(tmp_path):
    tasks = _tasks_dir(tmp_path)
    (tasks / "t-bad.json").write_text("{not json")
    cfg = Config(concierge_home=str(tmp_path / "ch"))
    items, warnings = collect_concierge(cfg, NOW)
    assert items == []
    assert any("t-bad.json" in w for w in warnings)


# --------------------------------------------------------------------------- #
# prs collector (injected gh runner)
# --------------------------------------------------------------------------- #
def test_prs_flags_old_via_injected_runner():
    fixture = {
        "me/repo": [
            {"number": 1, "title": "fresh", "createdAt": _iso(2),
             "url": "https://gh/1"},
            {"number": 2, "title": "stale", "createdAt": _iso(20),
             "url": "https://gh/2"},
        ]
    }
    cfg = Config(github_repos=["me/repo"], pr_age_warn_days=7)
    items, warnings = collect_prs(cfg, NOW, runner=lambda repo: fixture[repo])
    assert warnings == []
    by_id = {i["id"]: i for i in items}
    assert "OLD" not in by_id["pr:me/repo#1"]["detail"]
    assert "OLD" in by_id["pr:me/repo#2"]["detail"]
    assert by_id["pr:me/repo#2"]["age_days"] == pytest.approx(20, abs=0.01)


def test_prs_missing_gh_warns():
    def boom(repo):
        raise FileNotFoundError("gh")

    cfg = Config(github_repos=["me/repo"])
    items, warnings = collect_prs(cfg, NOW, runner=boom)
    assert items == []
    assert any("gh" in w for w in warnings)


def test_prs_gh_error_warns_per_repo():
    def boom(repo):
        raise RuntimeError("not authenticated")

    cfg = Config(github_repos=["a/b", "c/d"])
    items, warnings = collect_prs(cfg, NOW, runner=boom)
    assert items == []
    assert len(warnings) == 2


# --------------------------------------------------------------------------- #
# markers collector
# --------------------------------------------------------------------------- #
def test_markers_case_insensitive_one_item_per_line(tmp_path):
    base = tmp_path / "memory"
    (base / "sub").mkdir(parents=True)
    (base / "a.md").write_text(
        "intro\nThis is BLOCKED-ON-DANIEL: pick a name\nunrelated\n")
    (base / "sub" / "b.md").write_text(
        "standing UNTIL Daniel edits the goals file\n")
    (base / "c.txt").write_text("BLOCKED-ON-DANIEL but not markdown\n")

    cfg = Config(marker_paths=[str(base)])
    items, warnings = collect_markers(cfg, NOW)
    assert warnings == []
    titles = sorted(i["title"] for i in items)
    assert titles == ["This is BLOCKED-ON-DANIEL: pick a name",
                      "standing UNTIL Daniel edits the goals file"]
    assert all(i["kind"] == "markers" for i in items)


def test_markers_missing_path_warns(tmp_path):
    cfg = Config(marker_paths=[str(tmp_path / "gone")])
    items, warnings = collect_markers(cfg, NOW)
    assert items == []
    assert warnings and "not found" in warnings[0]


# --------------------------------------------------------------------------- #
# flares collector
# --------------------------------------------------------------------------- #
def _write_flare_log(records):
    log = flare.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_flares_filters_by_sev_and_staleness():
    _write_flare_log([
        {"ts": _iso(1), "sev": "warn", "msg": "recent warn", "source": "x",
         "host": "h"},
        {"ts": _iso(2), "sev": "info", "msg": "info ignored", "source": "x",
         "host": "h"},
        {"ts": _iso(20), "sev": "page", "msg": "too old", "source": "x",
         "host": "h"},
        {"ts": _iso(0), "sev": "page", "msg": "fresh page", "source": "y",
         "host": "h"},
        {"ts": _iso(0), "sev": "warn", "msg": "desk: some item", "source": "desk",
         "host": "h"},
    ])
    cfg = Config(flare_stale_days=7)
    items, warnings = collect_flares(cfg, NOW)
    titles = {i["title"] for i in items}
    assert titles == {"[warn] recent warn", "[page] fresh page"}
    assert warnings == []


def test_flares_no_log_is_empty():
    cfg = Config()
    items, warnings = collect_flares(cfg, NOW)
    assert items == [] and warnings == []


# --------------------------------------------------------------------------- #
# aggregate / render / digest
# --------------------------------------------------------------------------- #
def _full_config(tmp_path):
    tasks = _tasks_dir(tmp_path)
    (tasks / "t-9.json").write_text(json.dumps(
        {"id": "t-9", "title": "decide slug", "status": "blocked",
         "note": "n", "updated_at": _iso(3)}))
    base = tmp_path / "mem"
    base.mkdir()
    (base / "g.md").write_text("BLOCKED-ON-DANIEL: approve budget\n")
    _write_flare_log([
        {"ts": _iso(1), "sev": "page", "msg": "prod down", "source": "pod",
         "host": "h"},
    ])
    return Config(concierge_home=str(tmp_path / "ch"),
                  marker_paths=[str(base)], github_repos=["me/repo"])


def test_render_writes_inbox_with_sections_and_footer(tmp_path):
    cfg = _full_config(tmp_path)
    path = core.render(cfg, now=NOW, gh_runner=lambda repo: [])
    assert path == core.inbox_path()
    text = path.read_text()
    assert "# desk — waiting on Daniel" in text
    assert "Concierge" in text and "decide slug" in text
    assert "approve budget" in text
    assert "prod down" in text
    assert "generated 2026-08-16" in text


def test_digest_counts_and_oldest_three(tmp_path):
    cfg = _full_config(tmp_path)
    out = core.digest(cfg, now=NOW, gh_runner=lambda repo: [])
    assert "waiting on Daniel" in out
    assert "concierge=1" in out and "markers=1" in out and "flares=1" in out
    assert "oldest:" in out


# --------------------------------------------------------------------------- #
# sync state-diff (flare.send monkeypatched)
# --------------------------------------------------------------------------- #
def test_sync_flares_new_items_then_not_again(tmp_path, monkeypatch):
    cfg = _full_config(tmp_path)
    calls = []
    monkeypatch.setattr(flare, "send",
                        lambda msg, **kw: calls.append((msg, kw)) or {})

    r1 = core.sync(cfg, now=NOW, gh_runner=lambda repo: [])
    assert r1["new"] == 3 and r1["flared"] == 3
    assert len(calls) == 3
    assert all(kw["sev"] == "warn" and kw["source"] == "desk" for _, kw in calls)

    # second run, same items → no new flares
    calls.clear()
    r2 = core.sync(cfg, now=NOW, gh_runner=lambda repo: [])
    assert r2["new"] == 0 and r2["flared"] == 0
    assert calls == []


def test_sync_flares_only_the_newly_appearing_item(tmp_path, monkeypatch):
    cfg = _full_config(tmp_path)
    monkeypatch.setattr(flare, "send", lambda msg, **kw: {})
    core.sync(cfg, now=NOW, gh_runner=lambda repo: [])

    # a new marker appears → exactly one new flare
    (tmp_path / "mem" / "g.md").write_text(
        "BLOCKED-ON-DANIEL: approve budget\nBLOCKED-ON-DANIEL: also this\n")
    calls = []
    monkeypatch.setattr(flare, "send",
                        lambda msg, **kw: calls.append(msg) or {})
    r = core.sync(cfg, now=NOW, gh_runner=lambda repo: [])
    assert r["new"] == 1 and r["flared"] == 1
    assert "also this" in calls[0]


def test_state_persisted_between_syncs(tmp_path, monkeypatch):
    cfg = _full_config(tmp_path)
    monkeypatch.setattr(flare, "send", lambda msg, **kw: {})
    core.sync(cfg, now=NOW, gh_runner=lambda repo: [])
    state = json.loads(core.state_path().read_text())
    assert "ids" in state and len(state["ids"]) == 3
