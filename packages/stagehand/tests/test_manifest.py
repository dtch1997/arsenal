"""Unit tests for the manifest module — git capture (in and out of a repo),
manifest shape, the Flow.run() auto-write, and the ArtifactStore git stamp.
Git tests build their own throwaway repo under tmp_path so they don't depend
on this checkout's state."""
import asyncio
import json
import subprocess

from stagehand import (Flow, ArtifactStore, local_backend,
                       capture, write_manifest, git_info, git_stamp)
from stagehand import manifest as manifest_mod


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=tmp_path, check=True)
    return tmp_path


# ---- git capture ----------------------------------------------------------- #
def test_git_info_in_repo(tmp_path):
    repo = _git_repo(tmp_path)
    g = git_info(repo)
    assert g is not None
    assert len(g["sha"]) == 40
    assert g["dirty"] is False
    (repo / "x.txt").write_text("x")
    manifest_mod._git_cache.clear()            # bypass the per-process cache
    assert git_info(repo)["dirty"] is True


def test_git_info_outside_repo_is_none(tmp_path):
    (tmp_path / "plain").mkdir()
    assert git_info(tmp_path / "plain") is None
    assert git_stamp(tmp_path / "plain") is None


def test_git_stamp_is_compact(tmp_path):
    repo = _git_repo(tmp_path)
    manifest_mod._git_cache.clear()
    assert set(git_stamp(repo)) == {"sha", "dirty"}


# ---- capture / write ------------------------------------------------------- #
def test_capture_shape_and_config():
    m = capture(config={"lr": 3e-4}, flow={"title": "t"})
    for key in ("ts", "git", "argv", "cwd", "python", "host"):
        assert key in m
    assert m["config"] == {"lr": 3e-4}
    assert m["flow"] == {"title": "t"}
    assert "config" not in capture()           # omitted when not given


def test_write_manifest_round_trip(tmp_path):
    m = write_manifest(tmp_path / "deep" / "manifest.json", {"n": 2})
    on_disk = json.loads((tmp_path / "deep" / "manifest.json").read_text())
    assert on_disk["config"] == {"n": 2}
    assert on_disk["ts"] == m["ts"]


# ---- Flow integration ------------------------------------------------------ #
def test_flow_run_writes_manifest(tmp_path):
    f = Flow(runs_dir=tmp_path / "runs", config={"seed": 0}, title="sweep")
    f.map("double", [1, 2], lambda x: x * 2)
    asyncio.run(f.run())
    m = json.loads((tmp_path / "runs" / "manifest.json").read_text())
    assert m["config"] == {"seed": 0}
    assert m["flow"]["title"] == "sweep"
    assert m["flow"]["tasks"] == 2


def test_flow_without_runs_dir_writes_nothing(tmp_path):
    f = Flow()
    f.map("double", [1], lambda x: x * 2)
    asyncio.run(f.run())                       # no runs_dir ⇒ no manifest, no error


# ---- ArtifactStore integration --------------------------------------------- #
def test_put_stamps_git_meta(tmp_path):
    store = ArtifactStore(backend=local_backend(tmp_path / "blobs"),
                          cache_dir=tmp_path / "cache")
    (tmp_path / "a.txt").write_text("hello")
    art = store.put(tmp_path / "a.txt", name="a")
    assert "git" in art.meta                   # stamped (None outside a repo)
    explicit = store.put(tmp_path / "a.txt", name="b", meta={"git": "mine"})
    assert explicit.meta["git"] == "mine"      # caller's value wins
