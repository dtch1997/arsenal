"""flare tests — no network. The webhook post is monkeypatched everywhere it
would fire; the spam-guard clock is injected via ``send(now=...)``."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import flare
from flare import core


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point spool + config at a throwaway home; clear ambient context env."""
    monkeypatch.setenv("FLARE_HOME", str(tmp_path))
    monkeypatch.delenv("FLARE_WEBHOOK", raising=False)
    monkeypatch.delenv("FLARE_SLACK_TOKEN", raising=False)
    monkeypatch.delenv("FLARE_SLACK_CHANNEL", raising=False)
    monkeypatch.delenv("FLARE_LOG", raising=False)
    monkeypatch.delenv("FLARE_CONFIG", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CONCIERGE_TASK_ID", raising=False)
    return tmp_path


def _read_spool(home):
    path = home / ".flare" / "log.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_no_webhook_spools_and_exits_zero(isolated_home, capsys):
    rec = flare.send("hello", sev="info")
    assert rec["sent"] is False
    assert rec["suppressed"] is False
    out = capsys.readouterr().out
    assert "no webhook configured; spooled only" in out
    spool = _read_spool(isolated_home)
    assert len(spool) == 1
    assert spool[0]["msg"] == "hello"


def test_spool_record_shape(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")
    monkeypatch.setattr(core, "_post_slack", lambda url, text: None)
    rec = flare.send("boom", sev="page", source="pod-1")
    expected = {"ts", "sev", "msg", "source", "host", "cwd", "git_branch",
                "session_id", "task_id", "sent", "suppressed"}
    assert set(rec.keys()) == expected
    assert rec["sev"] == "page"
    assert rec["msg"] == "boom"
    assert rec["source"] == "pod-1"
    assert rec["sent"] is True
    # round-trips to the spool
    assert _read_spool(isolated_home)[0] == rec


def test_context_stamping_from_env(isolated_home, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-abc")
    monkeypatch.setenv("CONCIERGE_TASK_ID", "t-123")
    rec = flare.send("ctx")
    assert rec["session_id"] == "sess-abc"
    assert rec["task_id"] == "t-123"
    assert rec["host"]
    assert rec["cwd"]


def test_task_id_null_when_absent(isolated_home):
    rec = flare.send("no task")
    assert rec["task_id"] is None
    assert rec["session_id"] is None


def test_git_branch_stamped_in_repo(isolated_home, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    env={**__import__("os").environ, **env})
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    run("checkout", "-q", "-b", "feature-x")
    (repo / "f.txt").write_text("hi")
    run("add", "f.txt")
    run("commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    rec = flare.send("in repo")
    assert rec["git_branch"] == "feature-x"


def test_git_branch_null_outside_repo(isolated_home, tmp_path, monkeypatch):
    outside = tmp_path / "plain"
    outside.mkdir()
    monkeypatch.chdir(outside)
    rec = flare.send("no repo")
    assert rec["git_branch"] is None


def test_spam_guard_suppresses_duplicate(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")
    posts = []
    monkeypatch.setattr(core, "_post_slack", lambda url, text: posts.append(text))
    t0 = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

    first = flare.send("loop!", source="agent", now=t0)
    assert first["sent"] is True and first["suppressed"] is False

    # 5 minutes later, identical (msg, source) → suppressed, no post
    second = flare.send("loop!", source="agent", now=t0 + timedelta(minutes=5))
    assert second["sent"] is False and second["suppressed"] is True
    assert len(posts) == 1


def test_spam_guard_reposts_after_window(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")
    posts = []
    monkeypatch.setattr(core, "_post_slack", lambda url, text: posts.append(text))
    t0 = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

    flare.send("loop!", source="agent", now=t0)
    later = flare.send("loop!", source="agent", now=t0 + timedelta(minutes=11))
    assert later["sent"] is True and later["suppressed"] is False
    assert len(posts) == 2


def test_spam_guard_distinguishes_source(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")
    posts = []
    monkeypatch.setattr(core, "_post_slack", lambda url, text: posts.append(text))
    t0 = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)
    flare.send("same", source="a", now=t0)
    other = flare.send("same", source="b", now=t0 + timedelta(minutes=1))
    assert other["sent"] is True
    assert len(posts) == 2


def test_transport_failure_does_not_raise(isolated_home, monkeypatch, capsys):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")

    def boom(url, text):
        raise OSError("connection refused")

    monkeypatch.setattr(core, "_post_slack", boom)
    rec = flare.send("still alive", sev="warn")
    assert rec["sent"] is False
    assert "slack post failed" in capsys.readouterr().err
    # spooled despite the failure
    assert len(_read_spool(isolated_home)) == 1


def test_webhook_from_config_toml(isolated_home, monkeypatch):
    cfg = isolated_home / ".config" / "flare" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[slack]\nwebhook_url = "https://hooks.example/from-toml"\n')
    assert core.webhook_url() == "https://hooks.example/from-toml"
    # env wins over config
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/from-env")
    assert core.webhook_url() == "https://hooks.example/from-env"


def test_format_slack_has_headline_and_context():
    rec = {"sev": "page", "msg": "help", "host": "h", "cwd": "/c",
           "git_branch": "b", "session_id": "s", "task_id": "t"}
    text = core.format_slack(rec)
    lines = text.splitlines()
    assert lines[0] == "[page] help"
    assert "host h" in lines[1] and "branch b" in lines[1] and "task t" in lines[1]


def test_invalid_sev_rejected(isolated_home):
    with pytest.raises(ValueError):
        flare.send("x", sev="critical")


def test_cli_no_webhook_exits_zero(isolated_home):
    r = subprocess.run(
        [sys.executable, "-m", "flare.cli", "from cli", "--sev", "warn"],
        capture_output=True, text=True, env={**_child_env(isolated_home)},
    )
    assert r.returncode == 0, r.stderr
    assert "spooled only" in r.stdout
    assert len(_read_spool(isolated_home)) == 1


def _child_env(home):
    import os
    env = dict(os.environ)
    env["FLARE_HOME"] = str(home)
    env.pop("FLARE_WEBHOOK", None)
    env.pop("FLARE_SLACK_TOKEN", None)
    env.pop("FLARE_SLACK_CHANNEL", None)
    return env


def test_bot_token_transport_from_env(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("FLARE_SLACK_CHANNEL", "C123")
    posts = []
    monkeypatch.setattr(
        core, "_post_slack_bot",
        lambda token, channel, text: posts.append((token, channel, text)))
    rec = flare.send("via bot", sev="warn")
    assert rec["sent"] is True
    assert posts == [("xoxb-test", "C123", core.format_slack(rec))]


def test_webhook_wins_over_bot_token(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_WEBHOOK", "https://hooks.example/x")
    monkeypatch.setenv("FLARE_SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("FLARE_SLACK_CHANNEL", "C123")
    hooks, bots = [], []
    monkeypatch.setattr(core, "_post_slack", lambda url, text: hooks.append(url))
    monkeypatch.setattr(
        core, "_post_slack_bot", lambda *a: bots.append(a))
    rec = flare.send("prefer webhook")
    assert rec["sent"] is True and hooks and not bots


def test_bot_token_from_config(isolated_home, monkeypatch):
    cfg = isolated_home / ".config" / "flare" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[slack]\nbot_token = "xoxb-cfg"\nchannel = "C9"\n')
    assert core.bot_credentials() == ("xoxb-cfg", "C9")


def test_bot_token_needs_both_halves(isolated_home, monkeypatch):
    monkeypatch.setenv("FLARE_SLACK_TOKEN", "xoxb-test")
    assert core.bot_credentials() is None
    rec = flare.send("half-configured")
    assert rec["sent"] is False


def test_bot_api_ok_false_spools_not_sent(isolated_home, monkeypatch, capsys):
    monkeypatch.setenv("FLARE_SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("FLARE_SLACK_CHANNEL", "C123")

    def boom(token, channel, text):
        raise RuntimeError("chat.postMessage: channel_not_found")

    monkeypatch.setattr(core, "_post_slack_bot", boom)
    rec = flare.send("bad channel")
    assert rec["sent"] is False
    assert "spooled only" in capsys.readouterr().err
    assert len(_read_spool(isolated_home)) == 1
