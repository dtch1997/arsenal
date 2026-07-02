"""Tests for the Beads -> cairn importer."""

from __future__ import annotations

import json

import pytest

from cairn.beads import beads_record_to_issue, import_beads
from cairn.cli import main
from cairn.models import Status
from cairn.store import CairnError, Store


def _beads_record(**overrides) -> dict:
    rec = {
        "_type": "issue",
        "id": "stg-teu.2",
        "title": "Content-keyed step memoization",
        "description": "cache key = hash(inputs, params, source)",
        "acceptance_criteria": "re-run does zero recomputation",
        "notes": "shipped in PR #20",
        "status": "closed",
        "priority": 1,
        "issue_type": "feature",
        "owner": "dtch1997@users.noreply.github.com",
        "created_at": "2026-07-02T14:12:55Z",
        "updated_at": "2026-07-02T14:31:37Z",
        "closed_at": "2026-07-02T14:31:37Z",
        "dependencies": [
            {"issue_id": "stg-teu.2", "depends_on_id": "stg-teu", "type": "parent-child"}
        ],
    }
    rec.update(overrides)
    return rec


def test_record_maps_core_fields():
    issue = beads_record_to_issue(_beads_record())
    assert issue.id == "stg-teu.2"  # id preserved verbatim
    assert issue.title == "Content-keyed step memoization"
    assert issue.status == Status.CLOSED.value
    assert issue.type == "feature"
    assert issue.assignee == "dtch1997@users.noreply.github.com"
    assert issue.parent == "stg-teu"
    assert issue.closed_at == "2026-07-02T14:31:37Z"
    # acceptance_criteria + notes folded into the single description field
    assert "Acceptance criteria" in issue.description
    assert "re-run does zero recomputation" in issue.description
    assert "shipped in PR #20" in issue.description


def test_priority_clamped_from_beads_p4():
    issue = beads_record_to_issue(_beads_record(priority=4))
    assert issue.priority == 3  # cairn tops out at P3


def test_blocks_dependency_becomes_blocked_by():
    rec = _beads_record(
        dependencies=[
            {"issue_id": "stg-teu.2", "depends_on_id": "stg-aaa", "type": "blocks"},
            {"issue_id": "stg-teu.2", "depends_on_id": "stg-teu", "type": "parent-child"},
            {"issue_id": "stg-teu.2", "depends_on_id": "stg-xxx", "type": "related"},
        ]
    )
    issue = beads_record_to_issue(rec)
    assert issue.blocked_by == ["stg-aaa"]  # blocks -> blocked_by
    assert issue.parent == "stg-teu"  # parent-child -> parent
    # 'related' is intentionally dropped
    assert "stg-xxx" not in issue.blocked_by


def test_blocked_status_collapses_to_open():
    issue = beads_record_to_issue(_beads_record(status="blocked", closed_at=None))
    assert issue.status == Status.OPEN.value
    assert issue.closed_at is None


def test_non_issue_records_skipped():
    assert beads_record_to_issue({"_type": "comment", "id": "c1"}) is None
    assert beads_record_to_issue({"id": ""}) is None


def test_import_preserves_ready_semantics(tmp_path, monkeypatch):
    # A depends on (is blocked by) B; B is open -> only B is ready.
    export = tmp_path / "issues.jsonl"
    records = [
        {"_type": "issue", "id": "stg-a", "title": "A", "status": "open", "priority": 0,
         "dependencies": [{"issue_id": "stg-a", "depends_on_id": "stg-b", "type": "blocks"}]},
        {"_type": "issue", "id": "stg-b", "title": "B", "status": "open", "priority": 0,
         "dependencies": []},
    ]
    export.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    store = Store.init(tmp_path / "proj")
    result = import_beads(store, export)
    assert result.imported == 2
    ready_ids = {i.id for i in store.ready()}
    assert ready_ids == {"stg-b"}  # A blocked by open B

    store.close("stg-b")
    assert {i.id for i in store.ready()} == {"stg-a"}  # unblocked once B closes


def test_import_skip_existing(tmp_path):
    export = tmp_path / "issues.jsonl"
    export.write_text(
        json.dumps({"_type": "issue", "id": "stg-a", "title": "new", "status": "open"}) + "\n",
        encoding="utf-8",
    )
    store = Store.init(tmp_path / "proj")
    # seed an existing issue with the same id as the export record
    existing = store.create("keep me")
    existing.id = "stg-a"
    store.put(existing)

    result = import_beads(store, export, skip_existing=True)
    assert result.imported == 0 and result.skipped == 1
    assert store.get("stg-a").title == "keep me"  # untouched

    result = import_beads(store, export, skip_existing=False)
    assert result.imported == 1
    assert store.get("stg-a").title == "new"  # overwritten


def test_import_missing_file_errors(tmp_path):
    store = Store.init(tmp_path / "proj")
    with pytest.raises(CairnError):
        import_beads(store, tmp_path / "nope.jsonl")


def test_cli_import_roundtrip(tmp_path, monkeypatch, capsys):
    export = tmp_path / "issues.jsonl"
    export.write_text(
        json.dumps({"_type": "issue", "id": "smt-1", "title": "thing", "status": "open"}) + "\n",
        encoding="utf-8",
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    main(["init"])
    capsys.readouterr()
    assert main(["import", str(export), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["imported"] == 1
    assert out["ids"] == ["smt-1"]
