"""Delegation is queue-insertion with parentage: children are ordinary records
in the same home, the recursion rails (depth cap, budget carve) are enforced at
delegate time, and the probe fires only when ALL children are terminal."""
import pytest

from concierge import delegation
from concierge.records import Home, new_task, now_iso


def _home_parent(tmp_path, budget_usd=20.0, depth=0, model=None, priority=0):
    home = Home(tmp_path / "home")
    parent = new_task("t-parent", "parent", {"kind": "always"},
                      {"usd": budget_usd, "wall_minutes": 60},
                      {"repo": "git@example:r.git", "base": "main",
                       "branch": "pool/t-parent", "access": "readwrite"},
                      priority=priority, depth=depth, model=model)
    home.save(parent)
    return home, parent


def test_child_record_lineage_and_inheritance(tmp_path):
    home, parent = _home_parent(tmp_path, model="claude-fable-5", priority=1)

    child = delegation.delegate_child(home, parent, {}, title="leaf", spec="do X")

    saved = home.load(child["id"])
    assert saved["parent"] == "t-parent"
    assert saved["depth"] == 1
    assert saved["priority"] == 2                      # depth bonus: trees drain first
    assert saved["model"] == "claude-fable-5"          # inherit-by-default
    assert saved["status"] == "queued"
    assert saved["workspace"]["repo"] == "git@example:r.git"
    assert saved["workspace"]["base"] == "main"        # parent's base, not its branch
    assert saved["gate"] == {"kind": "always"}
    assert home.spec_path(child["id"]).read_text() == "do X"


def test_explicit_model_and_base_override_inheritance(tmp_path):
    home, parent = _home_parent(tmp_path, model="claude-fable-5")

    child = delegation.delegate_child(home, parent, {}, title="leaf", spec="s",
                                      model="claude-haiku-4-5", base="pool/t-parent")

    assert child["model"] == "claude-haiku-4-5"
    assert child["workspace"]["base"] == "pool/t-parent"


def test_depth_cap_refused(tmp_path):
    home, parent = _home_parent(tmp_path, depth=2)

    with pytest.raises(delegation.DelegationError, match="depth cap"):
        delegation.delegate_child(home, parent, {}, title="leaf", spec="s")


def test_depth_cap_configurable(tmp_path):
    home, parent = _home_parent(tmp_path, depth=2)

    child = delegation.delegate_child(home, parent, {"max_depth": 3},
                                      title="leaf", spec="s")
    assert child["depth"] == 3


def test_budget_carve_counts_spend_and_prior_children(tmp_path):
    home, parent = _home_parent(tmp_path, budget_usd=20.0)
    parent["attempts"].append({"n": 1, "pid": 1, "started": now_iso(),
                               "session_id": None, "cost_usd": 4.0,
                               "result": None, "log": "x"})
    delegation.delegate_child(home, parent, {}, title="c1", spec="s", budget_usd=10.0)

    # remaining envelope is 20 - 4 spent - 10 delegated = 6
    with pytest.raises(delegation.DelegationError, match="budget carve"):
        delegation.delegate_child(home, parent, {}, title="c2", spec="s", budget_usd=7.0)
    child = delegation.delegate_child(home, parent, {}, title="c2", spec="s")
    assert child["budget"]["usd"] == pytest.approx(6.0)  # default caps at what remains


def test_bad_gate_refused_good_gate_round_trips(tmp_path):
    home, parent = _home_parent(tmp_path)

    with pytest.raises(delegation.DelegationError, match="bad gate"):
        delegation.delegate_child(home, parent, {}, title="c", spec="s",
                                  gate={"kind": "no_such_gate"})
    child = delegation.delegate_child(home, parent, {}, title="c", spec="s",
                                      gate={"kind": "shell_ok", "cmd": "true"})
    assert child["gate"]["kind"] == "shell_ok"


def test_probe_children_exit_codes(tmp_path):
    home, parent = _home_parent(tmp_path)
    lines = []

    assert delegation.probe_children(home, "t-parent", out=lines.append) == 2

    c1 = delegation.delegate_child(home, parent, {}, title="c1", spec="s")
    c2 = delegation.delegate_child(home, parent, {}, title="c2", spec="s")
    assert delegation.probe_children(home, "t-parent", out=lines.append) == 1

    for tid, status in ((c1["id"], "done"), (c2["id"], "failed")):
        t = home.load(tid)
        t["status"] = status
        home.save(t)
    lines.clear()
    # ALL-terminal fires even with a failure — the parent handles failures
    assert delegation.probe_children(home, "t-parent", out=lines.append) == 0
    assert any("failed" in ln for ln in lines)  # outcome summary rides the wake message
