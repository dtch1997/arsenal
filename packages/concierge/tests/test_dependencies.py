"""Task dependencies (`after=`, issue #54): join-only parallel-then-join in the
reconciler. A dependent sits `held` (no worker slot, no concurrency seat) until
every dep is `done`, then releases to `queued`; if any dep ends not-done it fails
fast with `dependency <tid> ended <status>`. All state is derived from records on
disk every tick — restart-safe, no in-memory join bookkeeping."""
import pytest

from concierge import reconcile
from concierge.api import Pool
from concierge.records import ACTIVE, Home, new_task, now_iso


def _pool(tmp_path):
    return Pool(home=tmp_path / "home")


def _dep(pool, status="done", **kw):
    """Submit a bare task then force it to a terminal status on disk."""
    tid = pool.submit("do the thing", **kw)
    t = pool.get(tid)
    t["status"] = status
    pool.home.save(t)
    return tid


# -- submit-time validation --

def test_unknown_dependency_raises(tmp_path):
    pool = _pool(tmp_path)
    with pytest.raises(ValueError, match="unknown task"):
        pool.submit("polish", after=["t-nope-9999"])


def test_valid_dependencies_stored_and_held(tmp_path):
    pool = _pool(tmp_path)
    a = pool.submit("A")
    b = pool.submit("B")
    d = pool.submit("D polish", after=[a, b])

    rec = pool.get(d)
    assert rec["after"] == [a, b]                 # edges surfaced on the record
    assert rec["status"] == "held"
    assert a in rec["status_detail"] and b in rec["status_detail"]
    assert "held" in rec["status_detail"]         # tasks()/record visibility
    # a bare string is accepted as a one-element dep list
    e = pool.submit("E", after=a)
    assert pool.get(e)["after"] == [a]


def test_no_after_behaves_as_before(tmp_path):
    pool = _pool(tmp_path)
    tid = pool.submit("plain")
    rec = pool.get(tid)
    assert rec["after"] == []
    assert rec["status"] == "queued"


# -- release when all deps done --

def test_released_to_queued_when_all_deps_done(tmp_path):
    pool = _pool(tmp_path)
    a = _dep(pool, "done")
    b = pool.submit("B")                          # still queued (not done)
    d = pool.submit("D", after=[a, b])
    assert pool.get(d)["status"] == "held"

    # concurrency 0 → observe the held→queued transition without spawning workers
    reconcile.tick(pool.home, {"concurrency": 0})
    assert pool.get(d)["status"] == "held"        # b not done yet → still held
    assert b in pool.get(d)["status_detail"]      # narrowed to the unmet dep
    assert a not in pool.get(d)["status_detail"]

    bt = pool.get(b); bt["status"] = "done"; pool.home.save(bt)
    reconcile.tick(pool.home, {"concurrency": 0})
    assert pool.get(d)["status"] == "queued"      # released, ready to dispatch


# -- fail-fast propagation --

@pytest.mark.parametrize("bad", ["failed", "cancelled"])
def test_fail_fast_on_not_done_dependency(tmp_path, bad):
    pool = _pool(tmp_path)
    a = _dep(pool, "done")
    b = _dep(pool, bad)
    d = pool.submit("D", after=[a, b])

    reconcile.tick(pool.home, {})

    rec = pool.get(d)
    assert rec["status"] == "failed"
    assert rec["status_detail"] == f"dependency {b} ended {bad}"
    # fail-fast is NOT a gate failure: no strike, no gate_result written
    assert rec["gate_failures"] == 0
    assert rec["gate_result"] is None


def test_fail_fast_on_missing_dependency(tmp_path):
    pool = _pool(tmp_path)
    a = pool.submit("A")
    d = pool.submit("D", after=[a])
    pool.home.task_path(a).unlink()               # dep record vanished

    reconcile.tick(pool.home, {})
    assert pool.get(d)["status"] == "failed"
    assert "ended missing" in pool.get(d)["status_detail"]


# -- no slot / seat consumption while held --

def test_held_task_never_dispatched_even_with_free_slots(tmp_path, monkeypatch):
    pool = _pool(tmp_path)
    dispatched = []
    monkeypatch.setattr(reconcile, "_dispatch",
                        lambda home, cfg, task: dispatched.append(task["id"]))

    a = pool.submit("A")                          # queued
    d = pool.submit("D", after=[a])               # held

    reconcile.tick(pool.home, {"concurrency": 4})

    assert a in dispatched                         # the ready one dispatched
    assert d not in dispatched                     # the held one did not
    assert pool.get(d)["status"] == "held"


def test_held_is_active_so_daemon_and_waiters_treat_it_as_live(tmp_path):
    pool = _pool(tmp_path)
    a = pool.submit("A")
    d = pool.submit("D", after=[a])
    assert pool.get(d)["status"] in ACTIVE         # not terminal → wait() keeps polling


# -- backward compat: old records with no `after` key load & reconcile fine --

def test_legacy_record_without_after_key(tmp_path):
    home = Home(tmp_path / "home")
    task = new_task("t-legacy", "t", {"kind": "always"},
                    {"usd": 10, "wall_minutes": 60},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    del task["after"]                              # simulate a pre-#54 record on disk
    task["status"] = "queued"
    home.save(task)

    # reconciler must not KeyError on a record lacking `after`
    reconcile.tick(home, {"concurrency": 0})       # concurrency 0 → no dispatch attempt
    assert home.load("t-legacy")["status"] == "queued"
