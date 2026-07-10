"""Unit tests for the monitor primitive (file-backed progress + error state)."""
import json

import pytest

from stagehand.monitor import monitor, mark, read_monitors


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
