"""cli: --check exit codes and command dispatch (no model, no serving)."""

from __future__ import annotations

from threads import scan as scan_mod
from threads import weave as weave_mod
from threads.cli import main

from conftest import NOW


def _full_scan(env, canned_runner):
    env.add_transcript("proj", "s1", cwd="/h/jarvis", n_pairs=8,
                       tool_paths=["/h/jarvis/repos/safety-desert/a.py"])
    env.add_stub("safety-desert", body="repos/safety-desert")
    scan_mod.scan(days=30, runner=canned_runner, now=NOW)
    weave_mod.weave(runner=canned_runner, cluster=False)


def test_scan_check_exit_1_before_scan(env, capsys):
    assert main(["scan", "--check"]) == 1


def test_scan_check_exit_0_after_scan(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["scan", "--check"]) == 0
    assert "OK" in capsys.readouterr().out


def test_weave_check_exit_0_after_weave(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["weave", "--check"]) == 0


def test_render_prints_digest(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["render"]) == 0
    assert "activity dashboard" in capsys.readouterr().out


def test_vault_cmd_writes_index(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["vault"]) == 0
    out = capsys.readouterr().out
    assert "vault:" in out
    from threads import config
    assert (config.vault_dir() / "INDEX.md").exists()


def test_render_sort_flag(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["render", "--sort", "name"]) == 0
    assert "Sorted by name" in capsys.readouterr().out


def test_render_filter_search_flag(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["render", "--filter-search", "zzz-no-match"]) == 0
    # nothing matches → the threads table is empty but the digest still renders
    assert "activity dashboard" in capsys.readouterr().out


def test_status_prints_summary(env, canned_runner, capsys):
    _full_scan(env, canned_runner)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "threads:" in out
    assert "scan --check" in out
