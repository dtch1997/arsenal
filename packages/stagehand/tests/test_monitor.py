"""Unit tests for the monitor primitive (file-backed progress + error state)."""
import json
import os
import subprocess
import sys

import pytest

from stagehand.monitor import (monitor, track, mark, read_monitors,
                               current_monitor, monitor_env, ENV_DIR, ENV_PARENT)


def _load(p):
    return json.loads(p.read_text())


def test_done_lifecycle(tmp_path):
    p = tmp_path / "a.progress.json"
    with monitor("a", total=3, path=p, min_interval=0, cleanup=False) as m:
        assert _load(p)["state"] == "running"
        m.update(loss=1.0)
        m.update()
        m.update()
    s = _load(p)
    assert s["state"] == "done" and s["done"] == 3
    assert s["extra"]["loss"] == 1.0 and s["ended"] is not None


def test_failure_records_error_and_reraises(tmp_path):
    p = tmp_path / "b.progress.json"
    with pytest.raises(ValueError):
        with monitor("b", total=2, path=p, min_interval=0, cleanup=False) as m:
            m.update()
            raise ValueError("boom")
    s = _load(p)
    assert s["state"] == "failed"
    assert "boom" in s["extra"]["error"] and s["done"] == 1   # progress preserved at point of failure


def test_cleanup_removes_file_on_success(tmp_path):
    p = tmp_path / "e.progress.json"
    with monitor("e", total=2, path=p, min_interval=0, cleanup=True) as m:
        assert p.exists()           # live while running
        m.update()
    assert not p.exists()           # ephemeral: gone once out of scope


def test_cleanup_removes_file_on_failure(tmp_path):
    p = tmp_path / "f.progress.json"
    with pytest.raises(ValueError):
        with monitor("f", total=1, path=p, min_interval=0, cleanup=True):
            raise ValueError("boom")
    assert not p.exists()           # removed even on failure; exception still propagated


def test_cleanup_default_on_removes_file(tmp_path):
    p = tmp_path / "g.progress.json"
    with monitor("g", total=1, path=p, min_interval=0) as m:
        assert p.exists()           # live while running
        m.update()
    assert not p.exists()           # default is ephemeral: file gone on exit

def test_persist_opt_in_preserves_file(tmp_path):
    p = tmp_path / "h.progress.json"
    with monitor("h", total=1, path=p, min_interval=0, cleanup=False):
        pass
    assert p.exists() and _load(p)["state"] == "done"   # cleanup=False persists final state


def test_set_without_advancing(tmp_path):
    p = tmp_path / "c.progress.json"
    with monitor("c", total=1, path=p, min_interval=0, cleanup=False) as m:
        m.set(accept=0.7, reject=0.3)
        assert _load(p)["done"] == 0
    assert _load(p)["extra"]["accept"] == 0.7


def test_mark_patches_after_close(tmp_path):
    p = tmp_path / "d.progress.json"
    with monitor("d", total=1, path=p, min_interval=0, cleanup=False) as m:
        m.update()
    mark(p, state="failed", extra={"error": "gate: no checkpoint"})
    s = _load(p)
    assert s["state"] == "failed" and "gate" in s["extra"]["error"]


def test_mark_missing_file_is_noop(tmp_path):
    mark(tmp_path / "nope.progress.json", state="failed")   # must not raise


def test_read_monitors_tree(tmp_path):
    (tmp_path / "cell").mkdir()
    with monitor("sweep", 1, tmp_path / "sweep.progress.json", min_interval=0, cleanup=False):
        pass
    with monitor("cell", 1, tmp_path / "cell" / "train.progress.json", parent="sweep",
                 min_interval=0, cleanup=False):
        pass
    ms = {m["name"]: m for m in read_monitors(tmp_path)}
    assert set(ms) == {"sweep", "cell"}
    assert ms["cell"]["parent"] == "sweep"


# --- track: the loop-shaped wrapper ----------------------------------------- #

def test_track_ticks_per_item_and_infers_total(tmp_path):
    p = tmp_path / "train.progress.json"
    t = track(range(4), "train", path=p, min_interval=0, cleanup=False)
    for i in t:
        t.set(loss=1.0 / (i + 1))
    s = _load(p)
    assert s["state"] == "done" and s["done"] == 4 and s["total"] == 4
    assert s["extra"]["loss"] == 0.25            # last ride-along value persisted


def test_track_generator_has_indeterminate_total(tmp_path):
    p = tmp_path / "gen.progress.json"
    for _ in track((x for x in "ab"), "gen", path=p, min_interval=0, cleanup=False):
        pass
    s = _load(p)
    assert s["total"] is None and s["done"] == 2 and s["state"] == "done"


def test_track_failure_in_loop_body_records_and_reraises(tmp_path):
    p = tmp_path / "boom.progress.json"
    t = track(range(3), "boom", path=p, min_interval=0, cleanup=False)
    with pytest.raises(ValueError):
        for i in t:
            if i == 1:
                raise ValueError("boom")
    s = _load(p)
    # the caller's exception isn't visible inside the generator — the tracker
    # records that the loop stopped early, and the caller still sees the raise
    assert s["state"] == "failed" and s["done"] == 1
    assert "stopped early" in s["extra"]["error"]


def test_track_set_before_iteration_is_buffered(tmp_path):
    p = tmp_path / "buf.progress.json"
    t = track([1], "buf", path=p, min_interval=0, cleanup=False)
    t.set(lr=3e-4)                               # before the monitor opens
    for _ in t:
        pass
    assert _load(p)["extra"]["lr"] == 3e-4


# --- nesting: contextvar + env linkage --------------------------------------- #

def test_nested_monitor_auto_parents_alongside(tmp_path):
    outer = tmp_path / "task.progress.json"
    with monitor("distill/0", total=1, path=outer, min_interval=0, cleanup=False):
        with monitor("train", total=2, min_interval=0, cleanup=False) as m:
            assert m.path == tmp_path / "train.progress.json"   # sibling file
            m.update(); m.update()
    ms = {m["name"]: m for m in read_monitors(tmp_path)}
    assert ms["train"]["parent"] == "distill/0"
    assert current_monitor() is None             # contextvar reset on exit


def test_monitor_env_links_and_passes_through(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_DIR, raising=False)
    monkeypatch.delenv(ENV_PARENT, raising=False)
    assert monitor_env() == {}                   # nothing open, nothing linked
    with monitor("task/3", total=1, path=tmp_path / "t.progress.json",
                 min_interval=0):
        env = monitor_env()
    assert env == {ENV_DIR: str(tmp_path), ENV_PARENT: "task/3"}
    monkeypatch.setenv(ENV_DIR, str(tmp_path))   # inside a linked subprocess:
    monkeypatch.setenv(ENV_PARENT, "task/3")     # linkage passes through as-is
    assert monitor_env() == {ENV_DIR: str(tmp_path), ENV_PARENT: "task/3"}


def test_env_linked_monitor_resolves_path_and_parent(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DIR, str(tmp_path))
    monkeypatch.setenv(ENV_PARENT, "task/7")
    with monitor("train", total=1, min_interval=0, cleanup=False) as m:
        m.update()
    s = _load(tmp_path / "train.progress.json")
    assert s["parent"] == "task/7" and s["state"] == "done"


def test_subprocess_child_nests_under_parent(tmp_path):
    child = ("import os\n"
             "from stagehand.monitor import track\n"
             "t = track(range(3), 'train', min_interval=0, cleanup=False)\n"
             "for i in t: t.set(loss=i)\n")
    with monitor("task/0", total=1, path=tmp_path / "t.progress.json",
                 min_interval=0) as m:
        r = subprocess.run([sys.executable, "-c", child],
                           env={**os.environ, **monitor_env()},
                           capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = _load(tmp_path / "train.progress.json")
    assert s["parent"] == "task/0" and s["done"] == 3 and s["extra"]["loss"] == 2


# --- the status-flag anti-pattern is called out ------------------------------ #

def test_total_one_status_flag_warns(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="stagehand"):
        with monitor("cell", total=1, path=tmp_path / "c.progress.json",
                     min_interval=0) as m:
            m.set(status="running")
            m.set(status="done"); m.update()
    assert any("status flag" in r.message for r in caplog.records)


def test_real_loop_does_not_warn(tmp_path, caplog):
    with caplog.at_level("WARNING", logger="stagehand"):
        with monitor("train", total=3, path=tmp_path / "t.progress.json",
                     min_interval=0) as m:
            for _ in range(3):
                m.update(loss=0.1)
    assert not caplog.records
