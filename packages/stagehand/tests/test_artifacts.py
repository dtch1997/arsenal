"""Unit tests for the artifacts module — content-addressing, dir round-trips,
secrets, lineage, and produced_by capture inside a flow. Uses local_backend so
nothing touches GCS; the async flow test drives its own loop via asyncio.run."""
import asyncio
import json

from stagehand import ArtifactStore, Artifact, local_backend
from stagehand import Flow


def _store(tmp_path, **kw):
    return ArtifactStore(backend=local_backend(tmp_path / "blobs"),
                         cache_dir=tmp_path / "cache", **kw)


# ---- content-addressing & dedup ------------------------------------------- #
def test_same_bytes_same_id_diff_bytes_diff_id(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("hello")     # identical content
    (tmp_path / "c.txt").write_text("world")
    a = store.put(tmp_path / "a.txt", name="a")
    b = store.put(tmp_path / "b.txt", name="b")
    c = store.put(tmp_path / "c.txt", name="c")
    assert a.id == b.id            # same bytes ⇒ same id (dedup)
    assert a.id != c.id
    assert a.uri is not None


def test_file_round_trip(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "f.txt").write_text("payload")
    art = store.put(tmp_path / "f.txt", name="f")
    out = store.path(art)
    assert out.read_text() == "payload"
    assert store.path(art) == out  # cached by id


# ---- directories: deterministic tar, content-addressed -------------------- #
def _make_dir(root, contents):
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in contents.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_dir_is_content_addressed_and_deterministic(tmp_path):
    store = _store(tmp_path)
    d1 = _make_dir(tmp_path / "adapter1", {"w.bin": "weights", "cfg/c.json": "{}"})
    d2 = _make_dir(tmp_path / "adapter2", {"w.bin": "weights", "cfg/c.json": "{}"})
    a1 = store.put(d1, name="lora")
    a2 = store.put(d2, name="lora")
    assert a1.kind == "dir"
    assert a1.id == a2.id          # identical contents ⇒ identical id (mtime-independent)
    d3 = _make_dir(tmp_path / "adapter3", {"w.bin": "different", "cfg/c.json": "{}"})
    assert store.put(d3, name="lora").id != a1.id


def test_dir_round_trip(tmp_path):
    store = _store(tmp_path)
    d = _make_dir(tmp_path / "ckpt", {"a.bin": "AAA", "sub/b.bin": "BBB"})
    art = store.put(d, name="ckpt")
    out = store.path(art)
    assert (out / "a.bin").read_text() == "AAA"
    assert (out / "sub" / "b.bin").read_text() == "BBB"


# ---- secrets: ref-only, never uploaded ------------------------------------ #
def test_secret_is_ref_only(tmp_path, monkeypatch):
    store = _store(tmp_path)
    sec = store.secret("MY_API_KEY")
    assert sec.kind == "secret" and sec.uri is None
    assert sec.id == "env:MY_API_KEY"
    assert not (tmp_path / "blobs").exists() or not any((tmp_path / "blobs").iterdir())
    monkeypatch.setenv("MY_API_KEY", "sk-123")
    assert store.value(sec) == "sk-123"
    try:
        store.path(sec)            # secrets have no bytes to materialize
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---- lineage + persistence ------------------------------------------------ #
def test_lineage_and_lock_round_trip(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "data.jsonl").write_text("rows")
    (tmp_path / "cfg.yaml").write_text("lr: 1e-4")
    ds = store.put(tmp_path / "data.jsonl", name="train-data")
    cfg = store.put(tmp_path / "cfg.yaml", name="config")
    key = store.secret("HF_TOKEN")
    d = _make_dir(tmp_path / "out", {"adapter.bin": "w"})
    adapter = store.put(d, name="lora", inputs=[ds, cfg, key])
    assert set(adapter.inputs) == {ds.id, cfg.id, key.id}

    lin = store.lineage(adapter)
    assert set(lin) == {adapter.id, ds.id, cfg.id, key.id}

    lock = store.save(tmp_path / "artifacts.lock.json")
    reloaded = ArtifactStore.load(lock, backend=local_backend(tmp_path / "blobs"),
                                  cache_dir=tmp_path / "cache")
    assert set(reloaded.lineage(adapter.id)) == set(lin)
    # the materialized bytes are still resolvable by id after reload
    assert (reloaded.path(reloaded.get(adapter.id)) / "adapter.bin").read_text() == "w"


def test_registry_path_auto_flush(tmp_path):
    reg = tmp_path / "runs" / "artifacts.json"
    store = _store(tmp_path, registry_path=reg)
    (tmp_path / "f.txt").write_text("x")
    art = store.put(tmp_path / "f.txt", name="f")
    data = json.loads(reg.read_text())
    assert [a["id"] for a in data["artifacts"]] == [art.id]


# ---- produced_by capture inside a flow ------------------------------------ #
def test_produced_by_stamped_from_running_task(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "out.txt"

    async def train(_seed):
        src.write_text("trained")
        return store.put(src, name="model")

    async def body():
        f = Flow(tmp_path / "runs", title="t")
        h = f.spawn(train, (0,), name="train")
        await f.run()
        return h.result

    art = asyncio.run(body())
    assert isinstance(art, Artifact)
    assert art.produced_by == "train/0"
