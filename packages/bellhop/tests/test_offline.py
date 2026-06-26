"""Offline unit tests — pure logic, no live pod."""

import os

import pytest

from bellhop import (
    PodConfig,
    PreflightError,
    ProvisionError,
    RemoteJobError,
    RunpodError,
)
from bellhop.run import _is_git


def _cfg(**kw):
    # supply a fake key pair so pubkey/key resolution doesn't touch the real ~/.ssh
    return PodConfig(**kw)


def test_image_resolution_default_gpu():
    assert "pytorch" in _cfg(compute="gpu", gpu_id="x").resolve_image()


def test_image_resolution_preset_and_freeform():
    assert _cfg(image_preset="cpu-base", compute="cpu").resolve_image() == "runpod/base:1.0.2-ubuntu2204"
    assert _cfg(image="my/custom:1", image_preset="cpu-base").resolve_image() == "my/custom:1"


def test_unknown_preset_raises():
    with pytest.raises(PreflightError):
        _cfg(image_preset="does-not-exist").resolve_image()


def test_gpu_requires_gpu_id(tmp_path, monkeypatch):
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA test")
    cfg = _cfg(compute="gpu", gpu_id=None, ssh_key=str(key))
    with pytest.raises(PreflightError):
        cfg.to_create_body()


def test_create_body_shape(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA test")
    body = _cfg(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090", ssh_key=str(key)).to_create_body()
    assert body["gpuTypeIds"] == ["NVIDIA GeForce RTX 4090"]
    assert body["env"]["PUBLIC_KEY"].startswith("ssh-ed25519")
    assert body["ports"] == ["22/tcp"]
    assert body["cloudType"] == "COMMUNITY"


def test_missing_ssh_key_raises():
    with pytest.raises(PreflightError):
        _cfg(compute="gpu", gpu_id="x", ssh_key="/nope/missing").to_create_body()


def test_is_git_detection():
    assert _is_git("https://github.com/x/y")
    assert _is_git("git@github.com:x/y.git")
    assert not _is_git("./local/dir")


def test_error_exit_codes():
    assert PreflightError.exit_code == 10
    assert ProvisionError.exit_code == 20
    assert RemoteJobError("x", remote_exit=7).exit_code == 40
    assert RemoteJobError("x", remote_exit=7).remote_exit == 7
    assert issubclass(PreflightError, RunpodError)


def test_graphql_ttl_input(tmp_path):
    from datetime import timedelta
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA test")
    cfg = _cfg(compute="gpu", gpu_id="NVIDIA GeForce RTX 4090", ssh_key=str(key),
               stop_after=timedelta(hours=24), terminate_after=timedelta(hours=72))
    assert cfg.has_ttl()
    gi = cfg.to_graphql_input()
    # GraphQL shape differs from REST: singular gpuTypeId, ports string, env list
    assert gi["gpuTypeId"] == "NVIDIA GeForce RTX 4090"
    assert gi["ports"] == "22/tcp"
    assert {"key": "PUBLIC_KEY", "value": "ssh-ed25519 AAAA test"} in gi["env"]
    assert gi["stopAfter"].endswith("Z") and gi["terminateAfter"].endswith("Z")
    assert gi["stopAfter"] < gi["terminateAfter"]


def test_ttl_disabled_when_none(tmp_path):
    cfg = _cfg(compute="gpu", gpu_id="x", stop_after=None, terminate_after=None)
    assert not cfg.has_ttl()


def test_graphql_ttl_requires_gpu(tmp_path):
    from datetime import timedelta
    cfg = _cfg(compute="cpu", stop_after=timedelta(hours=1))
    with pytest.raises(PreflightError):
        cfg.to_graphql_input()


def test_cpu_with_ttl_still_builds_rest_body(tmp_path):
    # CPU + default TTL must NOT crash: it falls back to the REST path, which
    # has no native timer. Guards the routing fix in pod().
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA test")
    cfg = _cfg(compute="cpu", ssh_key=str(key))  # has_ttl() True by default
    assert cfg.has_ttl()
    body = cfg.to_create_body()
    assert body["computeType"] == "CPU"


def test_missing_api_key_raises(monkeypatch):
    from bellhop.rest import _api_key
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(PreflightError):
        _api_key(None)
