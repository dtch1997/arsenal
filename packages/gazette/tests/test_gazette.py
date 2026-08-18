from datetime import datetime, timedelta, timezone

import pytest

from gazette.config import Config
from gazette.lanes import (
    LABEL_AUTO,
    LABEL_BLOCKED,
    LABEL_DELAY,
    LABEL_VETO,
    PR,
    Lane,
    decide,
    path_matches,
    resolve_lane,
)
from gazette.notes import build_notes, compile_edition, digest, flare_body

NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def make_pr(**kw) -> PR:
    defaults = dict(
        repo="ArcadiaImpact/jarvis",
        number=1,
        title="t",
        url="https://example.com/1",
        author="agent",
        created_at=NOW - timedelta(hours=48),
        labels=[LABEL_AUTO],
        files=["docs/x.md"],
        checks="passing",
    )
    defaults.update(kw)
    return PR(**defaults)


# --------------------------------------------------------------------------- #
# path matching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path,glob,expected",
    [
        ("ops/cron.tab", "ops/**", True),
        ("ops", "ops/**", True),
        ("drops/cron.tab", "ops/**", False),
        ("CLAUDE.md", "CLAUDE.md", True),
        ("sub/dir/CLAUDE.md", "CLAUDE.md", True),  # bare names match at depth
        ("docs/a.md", "CLAUDE.md", False),
        ("conf/.env.prod", "**/.env*", True),
        (".env", "**/.env*", True),
        ("src/secrets.py", "**/*secret*", True),
    ],
)
def test_path_matches(path, glob, expected):
    assert path_matches(path, glob) is expected


# --------------------------------------------------------------------------- #
# lane resolution
# --------------------------------------------------------------------------- #
def test_lane_labels_respected():
    cfg = Config()
    assert resolve_lane([LABEL_AUTO], ["docs/a.md"], cfg)[0] is Lane.AUTO
    assert resolve_lane([LABEL_DELAY], ["docs/a.md"], cfg)[0] is Lane.DELAY
    assert resolve_lane([LABEL_BLOCKED], ["docs/a.md"], cfg)[0] is Lane.BLOCKED


def test_unlabeled_defaults_to_delay_with_reason():
    lane, reasons = resolve_lane([], ["docs/a.md"], Config())
    assert lane is Lane.DELAY
    assert any("unclassified" in r for r in reasons)


def test_auto_demoted_on_protected_paths():
    lane, reasons = resolve_lane([LABEL_AUTO], ["CLAUDE.md"], Config())
    assert lane is Lane.DELAY
    assert any("demoted to delay" in r for r in reasons)


def test_credential_paths_demote_to_blocked_regardless_of_label():
    for labels in ([LABEL_AUTO], [LABEL_DELAY], []):
        lane, reasons = resolve_lane(labels, ["conf/.env.prod"], Config())
        assert lane is Lane.BLOCKED
        assert any("demoted to blocked" in r for r in reasons)


# --------------------------------------------------------------------------- #
# merge decisions
# --------------------------------------------------------------------------- #
def test_auto_green_merges():
    assert decide(make_pr(), Config(), NOW).action == "merge"


def test_draft_and_veto_and_changes_requested_skip():
    cfg = Config()
    assert decide(make_pr(is_draft=True), cfg, NOW).action == "skip"
    assert decide(make_pr(labels=[LABEL_AUTO, LABEL_VETO]), cfg, NOW).action == "skip"
    assert decide(make_pr(review_decision="CHANGES_REQUESTED"), cfg, NOW).action == "skip"


def test_failing_or_pending_checks_wait():
    cfg = Config()
    d = decide(make_pr(checks="failing"), cfg, NOW)
    assert d.action == "wait" and "checks failing" in d.anomalies
    assert decide(make_pr(checks="pending"), cfg, NOW).action == "wait"


def test_delay_lane_wall_clock_fallback():
    # with no edition data (appearances=None) the old wall-clock window applies
    cfg = Config(delay_hours=36)
    young = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=10))
    old = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=40))
    assert decide(young, cfg, NOW).action == "wait"
    assert decide(old, cfg, NOW).action == "merge"


def test_delay_lane_counts_editions_not_hours():
    cfg = Config(delay_hours=36, delay_editions=2)
    pr = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=60))
    d = decide(pr, cfg, NOW, appearances=1)
    assert d.action == "wait" and "1/2" in d.reason  # 60h old but only 1 edition
    assert decide(pr, cfg, NOW, appearances=2).action == "merge"


def test_delay_lane_stall_anomaly_when_editions_never_arrive():
    cfg = Config(delay_hours=36, delay_editions=2)
    pr = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=120))
    d = decide(pr, cfg, NOW, appearances=0)
    assert d.action == "wait"
    assert any("notes cron" in a for a in d.anomalies)


def test_blocked_never_merges():
    pr = make_pr(labels=[LABEL_BLOCKED], created_at=NOW - timedelta(days=30))
    assert decide(pr, Config(), NOW).action == "skip"


# --------------------------------------------------------------------------- #
# notes rendering
# --------------------------------------------------------------------------- #
MERGED_ROW = {
    "repo": "ArcadiaImpact/jarvis", "number": 140, "title": "draft doc",
    "url": "u", "merged_at": NOW - timedelta(hours=3), "author": "agent",
    "labels": [LABEL_AUTO],
}


def _edition(appearances=None):
    cfg = Config()
    open_prs = [
        make_pr(number=141, title="CLAUDE.md tweak", labels=[LABEL_DELAY],
                created_at=NOW - timedelta(hours=5), files=["CLAUDE.md"]),
        make_pr(number=37, title="needs creds", labels=[LABEL_BLOCKED]),
    ]
    return cfg, open_prs, compile_edition(cfg, NOW, open_prs, [MERGED_ROW], appearances)


def test_notes_needs_you_first_with_default_outcomes():
    cfg, open_prs, ed = _edition(appearances={"jarvis#141": 1})
    text = build_notes(cfg, ed)
    assert text.index("## Needs you (2)") < text.index("## News")
    assert "jarvis#141" in text and "unless vetoed" in text and "--add-label veto" in text
    assert "jarvis#37" in text and "sits until you act" in text
    assert "jarvis#140" in text and "(auto)" not in text  # merged list drops lane tags
    assert sorted(ed.visible_refs) == ["jarvis#141", "jarvis#37"]
    d = digest(cfg, NOW, open_prs, [MERGED_ROW])
    assert "merged 1" in d and "1 in pipeline" in d and "1 waiting on you" in d


def test_notes_drafts_and_desk_and_news():
    cfg = Config()
    ed = compile_edition(cfg, NOW, [make_pr(is_draft=True)], [MERGED_ROW])
    assert ed.visible_refs == []  # drafts stay out of the edition entirely
    text = build_notes(cfg, ed, news="- you can now frobnicate (jarvis#140)",
                       desk_text="desk: 3 waiting")
    assert "you can now frobnicate" in text and "### All merges" in text
    assert "desk: 3 waiting" in text


def test_notes_quiet_state_and_flare_body():
    cfg = Config()
    ed = compile_edition(cfg, NOW, [], [])
    text = build_notes(cfg, ed)
    assert "nothing waits on you" in text and "nothing merged" in text
    assert "quiet" in flare_body(ed)  # empty morning is a one-liner

    _, _, busy = _edition(appearances={"jarvis#141": 2})
    body = flare_body(busy, spool_path="/spool/x.md")
    assert body.splitlines()[0].startswith("☀️")
    assert "NEEDS YOU (2)" in body and "merges tonight" in body
    assert "Full edition → /spool/x.md" in body


def test_editions_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GAZETTE_HOME", str(tmp_path))
    from gazette import editions

    editions.record_edition(NOW, ["jarvis#141", "arsenal#37"])
    editions.record_edition(NOW, ["jarvis#141"])  # same date — no double count
    editions.record_edition(NOW + timedelta(days=1), ["jarvis#141"])
    assert editions.load_appearances() == {"jarvis#141": 2, "arsenal#37": 1}


def test_synthesize_degrades():
    from gazette import synthesize

    merged = [dict(MERGED_ROW, number=n) for n in range(3)]
    assert "jarvis#1: draft doc" in synthesize.news_prompt(merged)
    assert synthesize.synthesize(Config(synthesis_cmd=""), merged) is None
    assert synthesize.synthesize(Config(synthesis_cmd="no-such-binary-xyz"), merged) is None
    assert synthesize.synthesize(Config(), merged[:2]) is None  # too few merges
