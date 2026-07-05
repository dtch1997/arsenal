"""A worked artifacts example — the lifecycle of inputs/outputs with lineage, with
the compute faked out so it runs anywhere in a second. Backed by `local_backend`
so it needs no GCS; swap in `cloudfs_backend()` for real remote storage.

    data ─┐
    config┼─> train ──> (lora adapter) ──> eval ──> (eval results)
    secret┘

Every artifact is content-addressed (id = hash of its bytes ⇒ dedup + immutable +
re-resolvable). `put` records `inputs` (lineage edges); inside a flow step it also
stamps `produced_by` (the run task). At the end we save a git-committable lock file
and reload it to walk the lineage — bytes still re-resolve by id. Run it:

    uv run python examples/artifacts.py
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path

from stagehand import ArtifactStore, local_backend, Flow


def _fresh(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- node fns (fake compute, real artifacts) ----------------------------- #
def train(data, *, store, cfg, key, work: Path):
    """Pretend to fine-tune: read the dataset, emit a LoRA adapter *directory*."""
    rows = store.path(data).read_text().splitlines()        # materialize the input
    adapter = _fresh(work / "adapter")
    (adapter / "adapter_model.safetensors").write_text(f"weights<{len(rows)} rows>")
    (adapter / "adapter_config.json").write_text('{"r": 16, "alpha": 32}')
    # a directory artifact, with full lineage (data + config + the secret used)
    return store.put(adapter, name="lora-adapter", inputs=[data, cfg, key],
                     meta={"rows": len(rows)})


def evaluate(adapter, *, store, work: Path):
    """Pretend to eval the adapter, emit an eval-results *file*."""
    _ = store.path(adapter)                                  # materialize the adapter dir
    results = work / "eval.json"
    results.write_text(json.dumps({"accuracy": 0.91, "n": 200}))
    return store.put(results, name="eval-results", inputs=[adapter])


async def main():
    runs = _fresh(Path("runs-artifacts"))
    work = _fresh(runs / "work")

    # local_backend keeps this hermetic; ArtifactStore() alone is cloudfs-backed.
    store = ArtifactStore(backend=local_backend(runs / "blobs"),
                          cache_dir=runs / "cache",
                          registry_path=runs / "artifacts.json")   # mirrored live

    # --- provide inputs upfront ------------------------------------------- #
    (work / "train.jsonl").write_text('{"q": "1+1"}\n{"q": "2+2"}\n{"q": "3+3"}\n')
    (work / "run.yaml").write_text("lr: 1e-4\nepochs: 1\n")
    data = store.put(work / "train.jsonl", name="train-data")
    cfg = store.put(work / "run.yaml", name="config")
    key = store.secret("HF_TOKEN")          # ref-only: the value is never uploaded

    # --- run the flow; artifacts flow through the handles ----------------- #
    f = Flow(runs / "flow", title="artifacts demo")
    lora = f.spawn(train, (data,), {"store": store, "cfg": cfg, "key": key,
                                    "work": work}, name="train")
    evl = f.spawn(evaluate, (lora,), {"store": store, "work": work}, name="eval")
    await f.run()

    adapter, eval_results = lora.result, evl.result
    print(f"adapter   : {adapter.name}  kind={adapter.kind}  id={adapter.id[:12]}…")
    print(f"            produced_by={adapter.produced_by}  uri={adapter.uri}")
    print(f"results   : {eval_results.name}  produced_by={eval_results.produced_by}")

    # materialize on demand (cached by id — a second call never re-fetches)
    got = store.path(eval_results)
    print(f"materialize: {got}  ->  {got.read_text()}")

    # --- persist a committable lineage pointer, then reload & walk it ----- #
    lock = store.save(runs / "artifacts.lock.json")
    reloaded = ArtifactStore.load(lock, backend=local_backend(runs / "blobs"),
                                  cache_dir=runs / "cache")
    print(f"\nlineage of {eval_results.name!r} (from {lock.name}):")
    for art in reloaded.lineage(eval_results.id).values():
        src = art.produced_by or "provided"
        print(f"  - {art.name:<13} {art.kind:<7} [{src}]")

    # content-addressing: re-putting identical bytes resolves to the same id
    again = store.put(work / "train.jsonl", name="dup-check")
    print(f"\ndedup     : same bytes ⇒ same id: {again.id == data.id}")
    print(f"registry mirrored at {runs / 'artifacts.json'} ; lock at {lock}")


if __name__ == "__main__":
    asyncio.run(main())
