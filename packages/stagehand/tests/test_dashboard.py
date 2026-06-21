"""Unit tests for the HTML dashboard renderer (pure; no I/O)."""
from stagehand.dashboard import render_dashboard, default_note, COLORS


def _m(name, state, done, total, parent=None, extra=None):
    return {"name": name, "parent": parent, "state": state, "done": done,
            "total": total, "extra": extra or {}}


def test_default_note_renders_extra_skipping_error():
    note = default_note({"loss": 0.5, "error": "boom", "acc": 0.7})
    assert "loss=0.5" in note and "acc=0.7" in note
    assert "boom" not in note   # error is shown in its own column, not the note


def test_render_includes_names_states_and_counts():
    monitors = [
        _m("sweep", "running", 0, 1),
        _m("cell_a", "done", 5, 5, parent="sweep"),
        _m("cell_b", "failed", 2, 5, parent="sweep", extra={"error": "no checkpoint"}),
    ]
    html = render_dashboard(monitors, started=0, title="my sweep")
    assert "my sweep" in html and "<title>my sweep</title>" in html
    for name in ("sweep", "cell_a", "cell_b"):
        assert name in html
    # chip counts: one of each state present
    assert "1 running" in html and "1 done" in html and "1 failed" in html
    # the failed unit surfaces its error and the failed colour
    assert "no checkpoint" in html and COLORS["failed"] in html


def test_children_indented_under_parent():
    monitors = [_m("root", "running", 0, 1),
                _m("child", "running", 0, 1, parent="root")]
    html = render_dashboard(monitors, started=0)
    # the child name carries indentation padding; the root name does not
    assert ">root<" in html                         # root unindented
    assert "&nbsp;" * 4 + "child" in html           # child indented one level


def test_orphan_with_unknown_parent_is_treated_as_root():
    # parent not present in the set -> rendered as a root, not dropped
    html = render_dashboard([_m("lonely", "running", 0, 1, parent="ghost")], started=0)
    assert "lonely" in html and "1 units" in html


def test_error_is_truncated():
    long = "x" * 200
    html = render_dashboard([_m("u", "failed", 0, 1, extra={"error": long})], started=0)
    assert "x" * 90 in html and "x" * 91 not in html
