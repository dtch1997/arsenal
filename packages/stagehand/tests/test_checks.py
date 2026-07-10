"""Unit tests for the reusable correctness predicates (stagehand.checks)."""
import asyncio
import json

from stagehand import Flow
from stagehand.checks import (CheckResult, ok, fail, require, exists, produced,
                              json_has, valid_image, finite, in_range, exit_ok)


# ---- CheckResult algebra -------------------------------------------------- #
def test_checkresult_is_the_gate_tuple_shape():
    r = produced(__file__)
    assert isinstance(r, tuple) and len(r) == 2
    is_ok, issues = r                          # unpacks as (ok, issues)
    assert is_ok is True and issues == []


def test_and_collects_all_failures():
    r = fail("a") & fail("b")
    assert not r.ok and r.issues == ["a", "b"]
    assert (ok() & ok()).ok
    assert not (ok() & fail("x")).ok


def test_or_passes_if_either():
    assert (fail("a") | ok()).ok
    r = fail("a") | fail("b")
    assert not r.ok and r.issues == ["a", "b"]


def test_invert_and_bool():
    assert (~fail("x")).ok
    assert not (~ok()).ok
    assert bool(ok()) and not bool(fail("x"))


def test_require_and_coercion():
    assert require(1 < 2, "nope").ok
    assert (ok() & (True, [])).ok                # coerces a raw (ok, issues) tuple
    assert not (ok() & False).ok                 # coerces a bare bool


# ---- file / artifact checks ----------------------------------------------- #
def test_produced(tmp_path):
    empty = tmp_path / "e.txt"
    empty.write_text("")
    full = tmp_path / "f.txt"
    full.write_text("data")
    assert produced(full).ok
    assert not produced(empty).ok                # exists but empty
    assert not produced(tmp_path / "missing").ok
    assert exists(empty).ok and not exists(tmp_path / "missing").ok


def test_json_has(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"loss": 0.1, "acc": 0.9}))
    assert json_has(p, ["loss", "acc"]).ok
    r = json_has(p, ["loss", "f1"])
    assert not r.ok and "f1" in r.issues[0]
    assert not json_has(tmp_path / "no.json", ["x"]).ok


def test_valid_image(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    svg = tmp_path / "a.svg"
    svg.write_text("<svg xmlns='...'></svg>")
    bad = tmp_path / "a.txt"
    bad.write_text("not an image")
    assert valid_image(png).ok and valid_image(svg).ok
    assert not valid_image(bad).ok


# ---- numbers -------------------------------------------------------------- #
def test_finite_catches_nan_inf_none():
    assert finite(0.5).ok and finite(3).ok
    assert not finite(float("nan")).ok
    assert not finite(float("inf")).ok
    assert not finite(None).ok
    assert not finite("x").ok


def test_in_range():
    assert in_range(0.5, 0, 1).ok
    assert not in_range(1.5, 0, 1).ok
    assert not in_range(float("nan"), 0, 1).ok


# ---- processes / outcomes ------------------------------------------------- #
def test_exit_ok_variants():
    assert exit_ok(0).ok and not exit_ok(1).ok

    class P:
        returncode = 0
    assert exit_ok(P()).ok

    class O:
        ok = False
    assert not exit_ok(O()).ok


# ---- composes as a real gate in a flow ------------------------------------ #
def test_checks_drive_a_filter(tmp_path):
    async def train(seed):
        ckpt = tmp_path / f"{seed}.ckpt"
        loss = float("nan") if seed == 1 else 0.2
        if seed != 2:                            # seed 2 writes nothing
            ckpt.write_text("w")
        return {"seed": seed, "ckpt": ckpt, "loss": loss}

    def healthy(r):
        return produced(r["ckpt"]) & finite(r["loss"])

    async def ev(r):
        return r["seed"]

    async def body():
        f = Flow()
        t = f.map("train", [0, 1, 2], train)
        good = f.filter("gate", t, healthy)
        f.map("eval", good, ev)
        state = await f.run()
        return state, good
    state, good = asyncio.run(body())
    assert [r["seed"] for r in good.results()] == [0]   # 1 diverged, 2 no ckpt
    assert state.failed == 2
