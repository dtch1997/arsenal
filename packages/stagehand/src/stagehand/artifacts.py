"""artifacts — content-addressed inputs/outputs with lineage for a `Flow`.

Provide artifacts *upfront* (datasets, configs, secrets, base adapters) and
persist *outputs* (adapters, eval results) without ever losing track of them.
Every artifact is identified by the **content hash of its bytes**, so the same
bytes always get the same id — immutable, dedup'd, and re-resolvable even if a
path moves. Each one records which artifacts it was derived from (`inputs`) and
which run/task produced it (`produced_by`); lineage is just that DAG.

Bytes live behind a **backend seam** so the core stays dependency-free:

  - `local_backend(root)` — a zero-dep content-addressed store on the local FS.
  - `cloudfs_backend(...)` — recommended: a content-addressed GCS store (`cloudfs`,
    imported lazily). This is the default when you don't pass a backend, so a
    plain `ArtifactStore()` persists to GCS; importing stagehand never needs
    `cloudfs`.

Directories (LoRA adapters, checkpoints) are tarred *deterministically* before
hashing, so a directory is content-addressed exactly like a file. Secrets store
only a reference to an env var — the value is never uploaded, but the secret still
shows up in the lineage graph.

    store = ArtifactStore()                                # cloudfs-backed
    ds  = store.put("data/train.jsonl", name="train-data")
    key = store.secret("OPENAI_API_KEY")
    # inside a flow step (produced_by is stamped automatically):
    adapter = store.put("out/adapter", name="lora", inputs=[ds, key])
    # later / elsewhere — materialize locally (cached by id, never re-downloaded):
    p = store.path(adapter)
    store.save("artifacts.lock.json")                     # commit this pointer
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .engine import current_monitor

_CHUNK = 1 << 20   # 1 MiB streaming, matches cloudfs


# --- the artifact ---------------------------------------------------------- #
@dataclass(frozen=True)
class Artifact:
    """An immutable, content-addressed pointer plus its lineage.

    `id` is the content hash of the bytes (`kind="file"`/`"dir"`) or `"env:<VAR>"`
    for a secret. `inputs` are the ids of the artifacts this was derived from and
    `produced_by` is the run task that made it (`"node/i"`), giving a lineage DAG.
    `uri` points at the backing blob (e.g. `gs://…`); None for secrets.
    """
    name: str
    id: str
    kind: str = "file"                          # file | dir | secret
    uri: str | None = None
    inputs: tuple[str, ...] = ()
    produced_by: str | None = None
    meta: dict = field(default_factory=dict, compare=False)


# --- backends -------------------------------------------------------------- #
# A backend is a dumb content-addressed blob store: put a file, get back its id;
# fetch a blob to a path by id. Tarring dirs / secret handling live in the store.
def local_backend(root):
    """Zero-dep content-addressed store under `root` on the local filesystem."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    class _Local:
        def put_file(self, path) -> str:
            fid = _md5_file(Path(path))
            dest = root / fid
            if not dest.exists():               # content-addressed ⇒ idempotent
                shutil.copyfile(path, dest)
            return fid

        def fetch(self, fid, dest) -> Path:
            src = root / fid
            if not src.exists():
                raise FileNotFoundError(f"artifact blob {fid!r} not in {root}")
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            return dest

        def exists(self, fid) -> bool:
            return (root / fid).exists()

        def uri(self, fid) -> str | None:
            p = root / fid
            return p.resolve().as_uri() if p.exists() else None

    return _Local()


def cloudfs_backend(bucket=None, prefix="daniel/jarvis/artifacts", **client_kw):
    """Recommended backend: a content-addressed GCS store via `cloudfs`.

    `cloudfs` is imported lazily on first use, so constructing this (and importing
    stagehand) never requires it — it's an optional integration, not a dep. The
    default `prefix` keeps artifacts under their own GCS namespace rather than
    cloudfs's default blob prefix."""
    state = {"c": None}

    def client():
        if state["c"] is None:
            from cloudfs import Client          # lazy: optional integration
            state["c"] = Client(bucket=bucket, prefix=prefix, **client_kw)
        return state["c"]

    class _Cloudfs:
        def put_file(self, path) -> str:
            return client().upload(str(path))

        def fetch(self, fid, dest) -> Path:
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            return Path(client().download(fid, str(dest)))

        def exists(self, fid) -> bool:
            return client().exists(fid)

        def uri(self, fid) -> str | None:
            return client().uri(fid)

    return _Cloudfs()


_default_backend = None


def set_default_artifact_backend(backend):
    """Set the backend an `ArtifactStore()` uses when none is passed (e.g. once at
    startup to a configured `cloudfs_backend(...)`)."""
    global _default_backend
    _default_backend = backend


def _resolve_default():
    return _default_backend if _default_backend is not None else cloudfs_backend()


# --- the store ------------------------------------------------------------- #
class ArtifactStore:
    """Registers artifacts, persists their bytes via a backend, and tracks lineage.

    Pass `registry_path` to mirror the registry to a JSON file on every change
    (e.g. `flow.runs_dir / "artifacts.json"`, so the live run keeps a record);
    `save()` writes the same shape to a path you commit as a lineage lock-file.
    `path()` / `value()` materialize an artifact locally on demand, cached by id.
    """

    def __init__(self, backend=None, *, registry_path=None, cache_dir=None):
        self.backend = backend or _resolve_default()
        self.registry_path = Path(registry_path) if registry_path else None
        self.cache_dir = (Path(cache_dir) if cache_dir
                          else Path(tempfile.gettempdir()) / "stagehand-artifacts")
        self._reg: dict[str, Artifact] = {}     # id -> Artifact

    # ---- register / produce ------------------------------------------------ #
    def put(self, path, name, *, inputs=(), meta=None, kind=None) -> Artifact:
        """Persist a local file or directory and register it. Directories are
        tarred deterministically before hashing so they're content-addressed too.
        Inside a flow step, `produced_by` is stamped from the running task."""
        path = Path(path)
        kind = kind or ("dir" if path.is_dir() else "file")
        if kind == "dir":
            tmp = Path(tempfile.mkstemp(suffix=".tar")[1])
            try:
                _tar_dir(path, tmp)
                fid = self.backend.put_file(tmp)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            fid = self.backend.put_file(path)
        return self._register(Artifact(
            name=name, id=fid, kind=kind, uri=self.backend.uri(fid),
            inputs=_ids(inputs), produced_by=_current_task(), meta=dict(meta or {})))

    def register_uri(self, uri, name, *, id=None, kind="file",
                     inputs=(), meta=None) -> Artifact:
        """Register an existing remote blob not produced through this store (e.g. a
        dataset you already have in GCS). `id` defaults to the uri if you don't
        have its content hash."""
        return self._register(Artifact(
            name=name, id=id or uri, kind=kind, uri=uri,
            inputs=_ids(inputs), produced_by=None, meta=dict(meta or {})))

    def secret(self, env_var, name=None, *, inputs=()) -> Artifact:
        """Register a secret as a *reference* to an env var. The value is never
        stored or uploaded, but the secret appears in the lineage graph."""
        return self._register(Artifact(
            name=name or env_var, id=f"env:{env_var}", kind="secret", uri=None,
            inputs=_ids(inputs), produced_by=_current_task(), meta={"env": env_var}))

    # ---- materialize ------------------------------------------------------- #
    def path(self, art) -> Path:
        """Return a local path to the artifact's bytes, fetching to the cache on
        first use (content-addressed ⇒ a cache hit never re-downloads). Dirs are
        extracted from their tar; for a file you get the cached file path."""
        art = self._get(art)
        if art.kind == "secret":
            raise ValueError(f"{art.name!r} is a secret — use value(), not path()")
        if art.kind == "dir":
            dest = self.cache_dir / "dirs" / art.id
            if (dest / ".ok").exists():
                return dest
            tmp = Path(tempfile.mkstemp(suffix=".tar")[1])
            try:
                self.backend.fetch(art.id, tmp)
                dest.mkdir(parents=True, exist_ok=True)
                with tarfile.open(tmp) as tar:
                    try:
                        tar.extractall(dest, filter="data")   # py3.12+ safe extract
                    except TypeError:
                        tar.extractall(dest)
                (dest / ".ok").write_text("")
            finally:
                tmp.unlink(missing_ok=True)
            return dest
        dest = self.cache_dir / "files" / art.id
        if not dest.exists():
            self.backend.fetch(art.id, dest)
        return dest

    def value(self, art) -> str:
        """Resolve a secret artifact's value from its env var at call time."""
        art = self._get(art)
        if art.kind != "secret":
            raise ValueError(f"{art.name!r} is not a secret")
        env = art.meta.get("env") or art.id.removeprefix("env:")
        try:
            return os.environ[env]
        except KeyError:
            raise KeyError(f"secret env var {env!r} is not set") from None

    # ---- lineage / persistence -------------------------------------------- #
    def get(self, id) -> Artifact:
        """The registered artifact with this id (raises if unknown)."""
        return self._reg[id]

    def lineage(self, art) -> dict[str, Artifact]:
        """The artifact plus the transitive closure of its `inputs` — the lineage
        subgraph as `{id: Artifact}` (only artifacts known to this store)."""
        art = self._get(art)
        seen: dict[str, Artifact] = {}
        stack = [art.id]
        while stack:
            i = stack.pop()
            if i in seen or i not in self._reg:
                continue
            a = self._reg[i]
            seen[i] = a
            stack.extend(a.inputs)
        return seen

    def to_dict(self) -> dict:
        return {"artifacts": [_to_dict(a) for a in self._reg.values()]}

    def save(self, path) -> Path:
        """Write the registry (every artifact + its lineage edges) to `path` —
        a small, git-committable pointer that re-resolves the whole DAG later."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        return path

    @classmethod
    def load(cls, path, *, backend=None, **kw) -> "ArtifactStore":
        """Reconstruct a store (artifacts + lineage) from a saved registry/lock."""
        store = cls(backend=backend, **kw)
        for d in json.loads(Path(path).read_text())["artifacts"]:
            store._register(_from_dict(d), flush=False)
        return store

    # ---- internals --------------------------------------------------------- #
    def _register(self, art: Artifact, *, flush=True) -> Artifact:
        self._reg[art.id] = art
        if flush and self.registry_path is not None:
            self.save(self.registry_path)
        return art

    def _get(self, art) -> Artifact:
        if isinstance(art, Artifact):
            return art
        return self._reg[art]


# --- helpers --------------------------------------------------------------- #
def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _tar_dir(src: Path, dest_tar: Path) -> Path:
    """Tar `src`'s files into `dest_tar` *deterministically*: sorted arcnames and
    normalized metadata (mtime/mode/owner) so identical contents hash identically
    regardless of when/where the dir was written."""
    files = sorted(p for p in src.rglob("*") if p.is_file())
    with tarfile.open(dest_tar, "w") as tar:
        for p in files:
            ti = tarfile.TarInfo(p.relative_to(src).as_posix())
            ti.size = p.stat().st_size
            ti.mtime = 0
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            with p.open("rb") as f:
                tar.addfile(ti, f)
    return dest_tar


def _ids(inputs) -> tuple[str, ...]:
    return tuple(x.id if isinstance(x, Artifact) else str(x) for x in inputs)


def _current_task() -> str | None:
    m = current_monitor()
    if m is None:
        return None
    try:
        return m.state.get("name")
    except Exception:
        return None


def _to_dict(a: Artifact) -> dict:
    d = asdict(a)
    d["inputs"] = list(a.inputs)
    return d


def _from_dict(d: dict) -> Artifact:
    return Artifact(
        name=d["name"], id=d["id"], kind=d.get("kind", "file"),
        uri=d.get("uri"), inputs=tuple(d.get("inputs", ())),
        produced_by=d.get("produced_by"), meta=dict(d.get("meta", {})))
