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
from gazette.notes import build_notes, digest

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


def test_delay_lane_respects_window():
    cfg = Config(delay_hours=36)
    young = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=10))
    old = make_pr(labels=[LABEL_DELAY], created_at=NOW - timedelta(hours=40))
    assert decide(young, cfg, NOW).action == "wait"
    assert decide(old, cfg, NOW).action == "merge"


def test_blocked_never_merges():
    pr = make_pr(labels=[LABEL_BLOCKED], created_at=NOW - timedelta(days=30))
    assert decide(pr, Config(), NOW).action == "skip"


# --------------------------------------------------------------------------- #
# notes rendering
# --------------------------------------------------------------------------- #
def test_notes_sections_and_digest():
    cfg = Config()
    open_prs = [
        make_pr(number=141, title="CLAUDE.md tweak", labels=[LABEL_DELAY],
                created_at=NOW - timedelta(hours=5), files=["CLAUDE.md"]),
        make_pr(number=37, title="needs creds", labels=[LABEL_BLOCKED]),
    ]
    merged = [{
        "repo": "ArcadiaImpact/jarvis", "number": 140, "title": "draft doc",
        "url": "u", "merged_at": NOW - timedelta(hours=3), "author": "agent",
        "labels": [LABEL_AUTO],
    }]
    text = build_notes(cfg, NOW, open_prs, merged)
    assert "jarvis#140" in text and "(auto)" in text
    assert "jarvis#141" in text and "veto window" in text
    assert "jarvis#37" in text and "Waiting on you" in text
    d = digest(cfg, NOW, open_prs, merged)
    assert "merged 1" in d and "1 in pipeline" in d and "1 waiting on you" in d


def test_notes_empty_state():
    text = build_notes(Config(), NOW, [], [])
    assert "nothing merged" in text and "queue is empty" in text
