"""provision.install_house_rules: HOUSE_RULES.md -> workspace AGENTS.md for the
Codex backend, kept out of worker PRs the same way the guard hook is
(.git/info/exclude when untracked, skip-worktree when tracked)."""
import subprocess

from concierge import provision
from concierge.records import Home


def _home_with_rules(tmp_path, text="# rules\n- be nice\n"):
    home = Home(tmp_path / "home")
    (home.root / "HOUSE_RULES.md").write_text(text)
    return home


def test_appends_rules_to_agents_md(tmp_path):
    home = _home_with_rules(tmp_path)
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    provision.install_house_rules(ws, home.root)
    body = (ws / "AGENTS.md").read_text()
    assert "# Pool house rules" in body
    assert "be nice" in body
    assert provision._HOUSE_MARKER in body


def test_idempotent(tmp_path):
    home = _home_with_rules(tmp_path)
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    provision.install_house_rules(ws, home.root)
    provision.install_house_rules(ws, home.root)
    body = (ws / "AGENTS.md").read_text()
    assert body.count(provision._HOUSE_MARKER) == 1   # not appended twice


def test_preserves_existing_agents_md(tmp_path):
    home = _home_with_rules(tmp_path)
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text("# Project agents\nrepo-specific stuff\n")
    provision.install_house_rules(ws, home.root)
    body = (ws / "AGENTS.md").read_text()
    assert "repo-specific stuff" in body              # original kept
    assert "# Pool house rules" in body               # rules appended after


def test_no_rules_file_is_noop(tmp_path):
    home = Home(tmp_path / "home")                     # no HOUSE_RULES.md
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    provision.install_house_rules(ws, home.root)
    assert not (ws / "AGENTS.md").exists()


def test_untracked_agents_md_excluded_from_git(tmp_path):
    home = _home_with_rules(tmp_path)
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    provision.install_house_rules(ws, home.root)
    exclude = (ws / ".git" / "info" / "exclude").read_text()
    assert "AGENTS.md" in exclude


def test_tracked_agents_md_skip_worktree(tmp_path):
    home = _home_with_rules(tmp_path)
    ws = home.workspace("t-x")
    ws.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)
    (ws / "AGENTS.md").write_text("# tracked agents\n")
    subprocess.run(["git", "-C", str(ws), "add", "AGENTS.md"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add agents"], check=True)

    provision.install_house_rules(ws, home.root)

    # skip-worktree bit set so the appended rules never show up as a diff
    r = subprocess.run(["git", "-C", str(ws), "ls-files", "-v", "AGENTS.md"],
                       capture_output=True, text=True)
    assert r.stdout.startswith("S")                   # 'S' = skip-worktree
    assert "# Pool house rules" in (ws / "AGENTS.md").read_text()
