"""vault: frontmatter validity, wikilink resolution, idempotent regen + prune."""

from __future__ import annotations

import re

from threads import config, dashboard, spool, vault, weave

from conftest import NOW


def _seed(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert",
                 memory_line="active experiment")
    write_record("s1", cwd="/h/jarvis",
                 hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                        "branches": [], "prs": []},
                 title="desert run one", keywords=["safety-desert", "sae"])
    write_record("u1", cwd="/h/jarvis", candidate_slugs=["nope"],
                 title="unfiled thing")
    weave.weave(runner=canned_runner, cluster=False, vault=False)


def _all_stems(root) -> set[str]:
    return {p.stem for p in root.rglob("*.md")}


def _split_frontmatter(text: str):
    assert text.startswith("---\n"), "note must open with YAML frontmatter"
    end = text.index("\n---", 4)
    return text[4:end], text[end:]


def test_vault_generates_notes_and_index(env, write_record, canned_runner):
    _seed(env, write_record, canned_runner)
    res = vault.write_vault(now=NOW)
    root = config.vault_dir()
    assert (root / "INDEX.md").exists()
    assert (root / "INDEX.md").stat().st_size > 0
    assert (root / "threads" / "safety-desert.md").exists()
    assert (root / "sessions" / "session-s1.md").exists()
    assert res.written > 0


def test_frontmatter_is_valid(env, write_record, canned_runner):
    _seed(env, write_record, canned_runner)
    vault.write_vault(now=NOW)
    root = config.vault_dir()
    for p in root.rglob("*.md"):
        if p.name == "INDEX.md":
            continue
        fm, _ = _split_frontmatter(p.read_text())
        for line in fm.strip().splitlines():
            # every frontmatter line is a `key: value` pair
            assert re.match(r"^[a-z_0-9]+:\s", line), f"bad frontmatter in {p}: {line!r}"


def test_wikilinks_resolve_within_vault(env, write_record, canned_runner):
    _seed(env, write_record, canned_runner)
    vault.write_vault(now=NOW)
    root = config.vault_dir()
    stems = _all_stems(root)
    targets = set()
    for p in root.rglob("*.md"):
        targets.update(re.findall(r"\[\[([^\]]+?)\]\]", p.read_text()))
    missing = {t for t in targets if t not in stems}
    assert not missing, f"dangling wikilinks: {missing}"


def test_thread_note_links_parent_and_sessions(env, write_record, canned_runner):
    env.add_stub("dogfight-rl")
    env.add_goal("dogfight-rl-release", mentions=["dogfight-rl"])
    write_record("d1", hints={"stubs": ["dogfight-rl"], "goals": [], "repos": [],
                              "branches": [], "prs": []}, title="rl step")
    weave.weave(runner=canned_runner, cluster=False, vault=False)
    vault.write_vault(now=NOW)
    note = (config.vault_dir() / "threads" / "dogfight-rl.md").read_text()
    assert "[[dogfight-rl-release]]" in note  # parent link
    assert "[[session-d1]]" in note
    assert (config.vault_dir() / "goals" / "dogfight-rl-release.md").exists()


def test_regeneration_idempotent_and_prunes(env, write_record, canned_runner):
    _seed(env, write_record, canned_runner)
    vault.write_vault(now=NOW)
    root = config.vault_dir()
    before = _all_stems(root)

    # a stale note whose source vanished must be pruned on regen
    stray = root / "threads" / "ghost-thread.md"
    stray.write_text("---\nslug: ghost-thread\n---\n")
    vault.write_vault(now=NOW)
    assert not stray.exists()
    assert _all_stems(root) == before  # identical set → idempotent

    # drop a real summary → its session note is pruned next regen
    spool.summary_path("s1").unlink()
    weave.weave(runner=canned_runner, cluster=False, vault=False)
    vault.write_vault(now=NOW)
    assert not (root / "sessions" / "session-s1.md").exists()


def test_weave_auto_writes_vault(env, write_record, canned_runner):
    env.add_stub("safety-desert", body="repos/safety-desert")
    write_record("s1", hints={"stubs": [], "goals": [], "repos": ["safety-desert"],
                              "branches": [], "prs": []})
    weave.weave(runner=canned_runner, cluster=False)  # vault=True by default
    assert (config.vault_dir() / "INDEX.md").exists()
