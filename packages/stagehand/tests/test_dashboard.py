"""Unit tests for the HTML dashboard renderer (pure; no I/O).

The renderer is deliberately generic: a status table of whatever monitor files
exist (always), plus a mermaid DAG when a `graph` topology is passed.
"""
from stagehand.dashboard import render_dashboard, default_note, COLORS


def _m(name, state, done, total, parent=None, extra=None):
    return {"name": name, "parent": parent, "state": state, "done": done,
            "total": total, "extra": extra or {}}


def _t(tid, node, state, deps=(), extra=None):
    """A task monitor as the engine writes it (id `node/i`, deps in meta)."""
    return {"name": tid, "parent": node, "state": state, "done": 1, "total": 1,
            "extra": extra or {}, "meta": {"node": node, "deps": list(deps)}}


def _node(name, kind, rank):
    return {"name": name, "kind": kind, "rank": rank}


# --- generic status table -------------------------------------------------- #
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


def test_table_is_present_without_a_graph_and_draws_no_dag():
    html = render_dashboard([_m("sweep", "running", 0, 1)], started=0)
    assert "table class=status" in html and "1 units" in html
    assert "flowchart LR" not in html               # no topology -> no DAG


# --- DAG panel (mermaid), when a graph topology is present ------------------ #
def _demo_graph():
    return {"title": "t",
            "nodes": [_node("train", "map", 0), _node("gate", "filter", 1),
                      _node("eval", "map", 2), _node("summary", "reduce", 3)],
            "edges": [["train", "gate"], ["gate", "eval"], ["eval", "summary"]]}


def _demo_tasks():
    # train_0/1/2 -> gate_0/1/2 -> eval (train_2 pruned at gate, so eval only 0/1)
    return [
        _t("train/0", "train", "done"), _t("train/1", "train", "done"),
        _t("train/2", "train", "done"),
        _t("gate/0", "gate", "done", deps=["train/0"]),
        _t("gate/1", "gate", "done", deps=["train/1"]),
        _t("gate/2", "gate", "failed", deps=["train/2"],
           extra={"error": "filtered: no checkpoint"}),
        _t("eval/0", "eval", "done", deps=["gate/0"]),
        _t("eval/1", "eval", "running", deps=["gate/1"]),
        _t("summary/0", "summary", "running", deps=["eval/0", "eval/1"]),
    ]


def test_graph_render_emits_mermaid_topology_with_edges():
    html = render_dashboard(_demo_tasks(), started=0, graph=_demo_graph())
    assert "flowchart LR" in html and "cdn.jsdelivr.net/npm/mermaid" in html
    # every node is a box and every edge is drawn (ids are sanitised node names)
    for n in ("train", "gate", "eval", "summary"):
        assert f'n_{n}[' in html
    assert "n_train --> n_gate" in html and "n_eval --> n_summary" in html


def test_status_table_lists_tasks_even_with_a_graph():
    html = render_dashboard(_demo_tasks(), started=0, graph=_demo_graph())
    # the generic table still lists every task, with no swimlane
    assert "table class=status" in html
    assert "class=swim" not in html        # the sweep-specific swimlane is gone
    for tid in ("train/0", "gate/2", "eval/1", "summary/0"):
        assert tid in html


def test_pruned_task_shown_distinctly_in_table():
    html = render_dashboard(_demo_tasks(), started=0, graph=_demo_graph())
    # gate/2 was filtered -> shows as pruned (its own colour), not a hard failure
    assert COLORS["pruned"] in html and ">pruned<" in html
    assert "1 pruned" in html              # chip count reflects it
