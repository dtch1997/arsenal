"""Unit tests for type annotations + flow.check() ("compile" the graph)."""
import asyncio
from typing import Optional

from stagehand import Flow, FlowCheckError, best_of


# --- a little typed domain ------------------------------------------------- #
class Cfg: ...
class Model: ...
class SubModel(Model): ...
class Eval: ...


async def train(cfg: Cfg) -> Model:
    return Model()

async def train_sub(cfg: Cfg) -> SubModel:
    return SubModel()

async def evaluate(m: Model) -> Eval:
    return Eval()

async def evaluate_str(s: str) -> Eval:        # wrong input type on purpose
    return Eval()

def is_ok(m: Model) -> bool:
    return True

def pick(es: list[Eval]) -> Eval:
    return es[0]

def shard_of(m: Model) -> list[str]:
    return ["a", "b"]


# --- well-typed graph passes ----------------------------------------------- #
def test_check_passes_on_well_typed_flow():
    f = Flow()
    t = f.map("train", [Cfg()], train)
    g = f.filter("gate", t, is_ok)
    e = f.map("eval", g, evaluate)
    f.reduce("pick", e, pick)
    f.check()                                   # no raise


def test_elem_types_propagate_through_edges():
    f = Flow()
    t = f.map("train", [Cfg()], train)
    assert t.elem_type is Model
    e = f.map("eval", t, evaluate)
    assert e.elem_type is Eval
    r = f.reduce("pick", e, pick)
    assert r.elem_type is Eval
    sh = f.expand("sh", t, shard_of)
    assert sh.elem_type is str                  # element of list[str]


def test_subclass_output_is_compatible():
    f = Flow()
    t = f.map("train", [Cfg()], train_sub)      # produces SubModel
    f.map("eval", t, evaluate)                   # wants Model — SubModel is fine
    f.check()


# --- mismatches are caught ------------------------------------------------- #
def test_check_catches_type_mismatch():
    f = Flow()
    t = f.map("train", [Cfg()], train)           # -> Model
    f.map("eval", t, evaluate_str)               # wants str
    try:
        f.check()
        assert False, "expected FlowCheckError"
    except FlowCheckError as e:
        assert "eval" in str(e) and "str" in str(e) and "Model" in str(e)


def test_check_catches_missing_dependency():
    f = Flow()
    f.add("b", lambda x: x, deps=["does_not_exist"])
    try:
        f.check()
        assert False, "expected FlowCheckError"
    except FlowCheckError as e:
        assert "missing" in str(e)


def test_run_check_raises_before_doing_any_work():
    ran = []

    async def t(cfg: Cfg) -> Model:
        ran.append("train")
        return Model()

    async def bad(s: str) -> Eval:               # wrong input type
        ran.append("eval")
        return Eval()

    f = Flow()
    tr = f.map("train", [Cfg()], t)
    f.map("eval", tr, bad)
    try:
        asyncio.run(f.run(check=True))
        assert False, "expected FlowCheckError"
    except FlowCheckError:
        pass
    assert ran == []                             # check ran before any task


# --- gradual: unannotated steps don't trip the checker --------------------- #
def test_unannotated_steps_are_gradual():
    async def a(x):
        return x

    async def b(x):
        return x

    f = Flow()
    t = f.map("a", [1], a)
    f.map("b", t, b)
    f.check()                                    # Any everywhere -> no complaints


def test_optional_input_accepts_concrete():
    async def opt(m: Optional[Model]) -> Eval:
        return Eval()
    f = Flow()
    t = f.map("train", [Cfg()], train)           # -> Model
    f.map("eval", t, opt)                         # wants Optional[Model]
    f.check()


# --- spawn surface is checked too ------------------------------------------ #
def make_cfg() -> Cfg:
    return Cfg()

async def ev(m: Model) -> Eval:
    return Eval()

async def sample(m: Model, *, attempt=0) -> Eval:
    return Eval()


def test_spawn_type_mismatch_is_caught():
    raised = False
    f = Flow()
    c = f.spawn(make_cfg, name="cfg")            # -> Cfg
    f.spawn(ev, (c,), name="eval")               # wants Model, gets Cfg
    try:
        f.check()
    except FlowCheckError:
        raised = True
    assert raised


def test_spawn_policy_checks_via_type_fn():
    raised = False
    f = Flow()
    c = f.spawn(make_cfg, name="cfg")            # -> Cfg
    f.spawn(best_of(sample, 2, score=lambda r: 0), (c,),
            name="sample", type_fn=sample)       # sample wants Model, gets Cfg
    try:
        f.check()
    except FlowCheckError:
        raised = True
    assert raised


def test_spawn_well_typed_passes():
    f = Flow()

    async def mk() -> Model:
        return Model()
    m = f.spawn(mk, name="mk")
    f.spawn(ev, (m,), name="eval")               # Model -> Model, fine
    f.check()
