"""Unit tests for the HTML dashboard renderer (pure; no I/O).

Two paths: the topology-aware render (mermaid DAG + swimlane, when a `graph` is
passed) and the legacy flat table (`graph=None`, kept as a fallback).
"""
import re

from stagehand.dashboard import render_dashboard, default_note, COLORS, GLYPH


def _swim(html):
    """The `<table class=swim>…</table>` region only (so column assertions don't
    accidentally match the mermaid block, which also labels every node)."""
    m = re.search(r"class=swim>(.*?)</table>", html, re.S)
    return m.group(1) if m else ""


def _m(name, state, done, total, parent=None, extra=None):
    return {"name": name, "parent": parent, "state": state, "done": done,
            "total": total, "extra": extra or {}}


def _t(tid, node, state, deps=(), extra=None):
    """A task monitor as the engine writes it (id `node/i`, deps in meta)."""
    return {"name": tid, "parent": node, "state": state, "done": 1, "total": 1,
            "extra": extra or {}, "meta": {"node": node, "deps": list(deps)}}


def _node(name, kind, rank):
    return {"name": name, "kind": kind, "rank": rank}


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


# --- topology-aware render (mermaid DAG + swimlane) ------------------------- #
def _demo_graph():
    return {"title": "t",
            "nodes": [_node("train", "task", 0), _node("gate", "task", 1),
                      _node("eval", "task", 2), _node("summary", "gather", 3)],
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


def test_swimlane_has_a_row_per_source_item_with_lineage():
    html = render_dashboard(_demo_tasks(), started=0, graph=_demo_graph())
    swim = _swim(html)
    assert swim
    # one row per train item (the spine roots), in order
    assert swim.index("train/0") < swim.index("train/1") < swim.index("train/2")
    # spine columns are the fanned-out nodes, not the barrier
    assert "train<br>" in swim and "eval<br>" in swim
    assert "summary" not in swim  # barrier is a footer band, not a column


def test_pruned_item_shows_pruned_glyph_and_blank_downstream():
    html = render_dashboard(_demo_tasks(), started=0, graph=_demo_graph())
    assert GLYPH["pruned"] in html and COLORS["pruned"] in html  # gate/2 pruned
    assert GLYPH["running"] in html                              # eval/1 running


def test_barrier_is_footer_band_and_singletons_are_cards():
    graph = {"title": "t",
             "nodes": [_node("train", "task", 0), _node("eval", "task", 1),
                       _node("summary", "gather", 2), _node("report", "task", 3)],
             "edges": [["train", "eval"], ["eval", "summary"], ["summary", "report"]]}
    tasks = [_t("train/0", "train", "done"), _t("train/1", "train", "done"),
             _t("eval/0", "eval", "done", deps=["train/0"]),
             _t("eval/1", "eval", "done", deps=["train/1"]),
             _t("summary/0", "summary", "done", deps=["eval/0", "eval/1"]),
             _t("report/0", "report", "done", deps=["summary/0"])]
    html = render_dashboard(tasks, started=0, graph=graph)
    assert "class=band" in html and "summary" in html      # gather -> footer band
    assert "class=card" in html and "report" in html       # singleton -> card
    # neither the barrier nor the singleton is a swimlane column
    swim = _swim(html)
    assert "report" not in swim and "summary" not in swim
    assert "train<br>" in swim and "eval<br>" in swim


def test_falls_back_to_flat_table_without_graph():
    html = render_dashboard([_m("sweep", "running", 0, 1)], started=0)
    assert "<table>" in html and "1 units" in html and "flowchart LR" not in html
