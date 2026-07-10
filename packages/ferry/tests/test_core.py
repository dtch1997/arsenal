"""Tests for the arg-building logic ferry owns. rclone is stubbed."""

import subprocess

import pytest

import ferry
from ferry import core


@pytest.fixture
def captured(monkeypatch):
    """Capture the argv ferry would hand to rclone, without running it."""
    calls = []

    def fake_run(cmd, text=True, capture_output=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(core.shutil, "which", lambda _: "/usr/bin/rclone")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    return calls


def test_push_is_copy_by_default(captured):
    ferry.push("data/", "gcs:bkt/data/", progress=False)
    cmd = captured[0]
    assert cmd[1:4] == ["copy", "data/", "gcs:bkt/data/"]
    assert "--progress" not in cmd


def test_push_mirror_uses_sync(captured):
    ferry.push("data/", "gcs:bkt/data/", mirror=True, progress=False)
    assert captured[0][1] == "sync"


def test_pull_swaps_src_dst(captured):
    ferry.pull("gcs:bkt/data/", "data/", progress=False)
    assert captured[0][1:4] == ["copy", "gcs:bkt/data/", "data/"]


def test_dry_run_and_filters_and_parallelism(captured):
    ferry.push(
        "d/", "gcs:b/d/",
        dry_run=True, excludes=["*.tmp"], includes=["*.json"],
        transfers=8, checkers=16, progress=False,
    )
    cmd = captured[0]
    assert "--dry-run" in cmd
    assert cmd[cmd.index("--exclude") + 1] == "*.tmp"
    assert cmd[cmd.index("--include") + 1] == "*.json"
    assert cmd[cmd.index("--transfers") + 1] == "8"
    assert cmd[cmd.index("--checkers") + 1] == "16"


def test_remote_maps_relative_path_under_base(captured):
    exp = ferry.Remote("gcs:bkt/experiments/foo")
    exp.push("results/", progress=False)
    assert captured[0][3] == "gcs:bkt/experiments/foo/results"


def test_remote_pull_maps_relative_path(captured):
    exp = ferry.Remote("gcs:bkt/experiments/foo")
    exp.pull("results", progress=False)
    assert captured[0][2] == "gcs:bkt/experiments/foo/results"


def test_remote_defaults_merge_and_override(captured):
    exp = ferry.Remote("gcs:bkt/foo", defaults={"excludes": ["*.tmp"], "progress": False})
    exp.push("d/")
    assert "*.tmp" in captured[0]
    # per-call kwargs override defaults
    captured.clear()
    exp.push("d/", excludes=[])
    assert "*.tmp" not in captured[0]


def test_remote_child(captured):
    exp = ferry.Remote("gcs:bkt/foo")
    exp.child("sub").push("d/", progress=False)
    assert captured[0][3] == "gcs:bkt/foo/sub/d"


def test_remote_rejects_local_base():
    with pytest.raises(ValueError):
        ferry.Remote("/local/path")


def test_is_remote_classification():
    assert core._is_remote("gcs:bkt/x")
    assert not core._is_remote("data/x")
    assert not core._is_remote("C:\\Users\\x")  # windows drive -> local
    assert not core._is_remote("./rel")


def test_missing_rclone_raises(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda _: None)
    with pytest.raises(ferry.RcloneNotFound):
        ferry.push("d/", "gcs:b/")
