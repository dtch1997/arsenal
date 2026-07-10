"""Tests for the cairn store, graph logic, and CLI."""

from __future__ import annotations

import json

import pytest

from cairn.cli import main
from cairn.models import Status
from cairn.store import CairnError, Store, find_root


@pytest.fixture
def store(tmp_path):
    return Store.init(tmp_path, prefix="cn")


def test_init_and_discover(tmp_path):
    Store.init(tmp_path, prefix="tk")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_root(sub) == tmp_path.resolve()
    assert Store.discover(sub).prefix == "tk"


def test_init_twice_fails(tmp_path):
    Store.init(tmp_path)
    with pytest.raises(CairnError):
        Store.init(tmp_path)


def test_create_get_roundtrip(store):
    issue = store.create("Fix the flux", description="details", priority=1, type="bug")
    assert issue.id.startswith("cn-")
    got = store.get(issue.id)
    assert got.title == "Fix the flux"
    assert got.description == "details"
    assert got.priority == 1
    assert got.type == "bug"
    assert got.status == Status.OPEN.value


def test_ids_are_unique_across_many_creates(store):
    ids = {store.create(f"task {i}").id for i in range(200)}
    assert len(ids) == 200


def test_ready_excludes_blocked_until_blocker_closed(store):
    a = store.create("blocker")
    b = store.create("blocked")
    store.add_dep(b.id, a.id)

    ready_ids = {i.id for i in store.ready()}
    assert a.id in ready_ids
    assert b.id not in ready_ids  # blocked by open a

    store.close(a.id)
    ready_ids = {i.id for i in store.ready()}
    assert b.id in ready_ids  # blocker closed -> now ready
    assert a.id not in ready_ids  # closed issues are never "ready"


def test_ready_sorted_by_priority_then_age(store):
    low = store.create("low", priority=3)
    high = store.create("high", priority=0)
    mid = store.create("mid", priority=1)
    order = [i.id for i in store.ready()]
    assert order == [high.id, mid.id, low.id]


def test_missing_blocker_does_not_block(store):
    b = store.create("blocked")
    # hand-inject a dangling dependency; a missing blocker should not block
    b.blocked_by.append("cn-dead")
    store._save(b)
    assert b.id in {i.id for i in store.ready()}


def test_claim_sets_assignee_and_status(store):
    issue = store.create("thing")
    claimed = store.claim(issue.id, "alice")
    assert claimed.assignee == "alice"
    assert claimed.status == Status.IN_PROGRESS.value


def test_close_sets_closed_at_and_reopen_clears_it(store):
    issue = store.create("thing")
    closed = store.close(issue.id)
    assert closed.status == Status.CLOSED.value
    assert closed.closed_at is not None
    reopened = store.reopen(issue.id)
    assert reopened.status == Status.OPEN.value
    assert reopened.closed_at is None


def test_self_dependency_rejected(store):
    a = store.create("a")
    with pytest.raises(CairnError):
        store.add_dep(a.id, a.id)


def test_dep_on_missing_blocker_rejected(store):
    a = store.create("a")
    with pytest.raises(CairnError):
        store.add_dep(a.id, "cn-zzzz")


def test_memory_roundtrip(store):
    store.remember("prefer reverse-KL")
    store.remember("Qwen3-30B reproduces")
    texts = [m["text"] for m in store.memories()]
    assert texts == ["prefer reverse-KL", "Qwen3-30B reproduces"]


def test_atomic_write_leaves_no_temp_files(store):
    store.create("thing")
    leftovers = list(store.issues_dir.glob("*.tmp*"))
    assert leftovers == []


# ---- CLI smoke tests -----------------------------------------------------
def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0

    assert main(["create", "First task", "-p", "0"]) == 0
    out = capsys.readouterr().out
    assert "created" in out

    assert main(["create", "Second task", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    second_id = created["id"]

    assert main(["ready", "--json"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert len(ready) == 2

    assert main(["claim", second_id, "--as", "bob"]) == 0
    capsys.readouterr()
    assert main(["ready", "--json"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert second_id not in {i["id"] for i in ready}  # in_progress != ready

    assert main(["close", second_id]) == 0
    capsys.readouterr()
    assert main(["show", second_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "closed"


def test_cli_prime_reports_ready_and_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    main(["create", "do the thing"])
    main(["remember", "watch the max_tokens confound"])
    capsys.readouterr()
    assert main(["prime"]) == 0
    out = capsys.readouterr().out
    assert "Ready now" in out
    assert "max_tokens confound" in out


def test_cli_missing_store_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["ready"]) == 1
    assert "cairn init" in capsys.readouterr().err
