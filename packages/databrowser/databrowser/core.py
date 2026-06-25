"""Core: load JSONL, validate the schema, and build a static HTML browser.

The public surface is small:

    build(data, out_dir, *, filter_fields=None, title=None, strict=True) -> Path
    validate_schema(records, *, strict=True) -> list[str]
    FilterField

`data` may be a path to a ``.jsonl`` / ``.json`` file, or an in-memory list of
dicts. `filter_fields` is the only knob most callers touch: by default nothing is
filterable; pass field names (or :class:`FilterField` specs) to expose filters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from numbers import Number
from pathlib import Path
from typing import Iterable, Sequence, Union

CATEGORICAL = "categorical"
CONTINUOUS = "continuous"


class SchemaError(ValueError):
    """Raised when records do not all share the same set of keys."""


@dataclass
class FilterField:
    """A field the browser should let you filter on.

    ``kind`` may be left ``None`` to infer it from the data: a field whose
    non-null values are all numeric becomes :data:`CONTINUOUS` (interval
    filter); anything else becomes :data:`CATEGORICAL` (subset-of-values
    filter).
    """

    name: str
    kind: Union[str, None] = None

    def __post_init__(self) -> None:
        if self.kind is not None and self.kind not in (CATEGORICAL, CONTINUOUS):
            raise ValueError(
                f"FilterField.kind must be {CATEGORICAL!r}, {CONTINUOUS!r} or None, "
                f"got {self.kind!r}"
            )


FilterSpec = Union[str, FilterField]


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def load_records(data: Union[str, Path, Iterable[dict]]) -> list[dict]:
    """Load records from a JSONL/JSON file path, or pass through an iterable.

    A ``.jsonl`` file is read one JSON object per non-blank line. A ``.json``
    file may hold either a top-level array or a single object (wrapped to a
    one-element list).
    """
    if isinstance(data, (str, Path)):
        path = Path(data)
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".json":
            parsed = json.loads(text)
            records = parsed if isinstance(parsed, list) else [parsed]
        else:  # treat everything else as JSONL
            records = []
            for lineno, line in enumerate(text.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path}:{lineno}: invalid JSON line: {exc}"
                    ) from exc
    else:
        records = list(data)

    for i, r in enumerate(records):
        if not isinstance(r, dict):
            raise SchemaError(
                f"record {i} is a {type(r).__name__}, not an object; "
                "databrowser requires every item to be a JSON object"
            )
    return records


# --------------------------------------------------------------------------- #
# schema validation
# --------------------------------------------------------------------------- #
def validate_schema(records: Sequence[dict], *, strict: bool = True) -> list[str]:
    """Check that every record has the same set of keys.

    Returns the canonical, order-preserving list of field names. In ``strict``
    mode a mismatch raises :class:`SchemaError`; otherwise the union of all keys
    is returned (first-seen order) and the mismatch is tolerated.
    """
    if not records:
        return []

    # First-seen key order, taken across all records so non-strict mode is stable.
    ordered: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in r:
            if k not in seen:
                seen.add(k)
                ordered.append(k)

    reference = set(records[0].keys())
    if strict:
        for i, r in enumerate(records):
            keys = set(r.keys())
            if keys != reference:
                missing = sorted(reference - keys)
                extra = sorted(keys - reference)
                detail = []
                if missing:
                    detail.append(f"missing {missing}")
                if extra:
                    detail.append(f"extra {extra}")
                raise SchemaError(
                    f"record {i} schema differs from record 0 "
                    f"({'; '.join(detail)}). "
                    f"Pass strict=False to browse anyway (union of keys)."
                )
        # In strict mode all records share record 0's keys, so use that order.
        return list(records[0].keys())
    return ordered


# --------------------------------------------------------------------------- #
# filter specs
# --------------------------------------------------------------------------- #
def _is_number(v) -> bool:
    return isinstance(v, Number) and not isinstance(v, bool)


def _infer_kind(records: Sequence[dict], name: str) -> str:
    """Numeric (non-bool) non-null values => continuous; else categorical."""
    saw_value = False
    for r in records:
        v = r.get(name)
        if v is None:
            continue
        saw_value = True
        if not _is_number(v):
            return CATEGORICAL
    return CONTINUOUS if saw_value else CATEGORICAL


def build_filter_specs(
    records: Sequence[dict],
    fields: Union[Sequence[FilterSpec], None],
    schema: Sequence[str],
) -> list[dict]:
    """Resolve user-supplied ``filter_fields`` into concrete filter specs.

    Each spec is a JSON-able dict the front-end consumes directly:

    * categorical -> ``{"name", "kind": "categorical", "values": [...]}``
    * continuous  -> ``{"name", "kind": "continuous", "min": x, "max": y}``
    """
    if not fields:
        return []

    schema_set = set(schema)
    specs: list[dict] = []
    for f in fields:
        ff = FilterField(f) if isinstance(f, str) else f
        if not isinstance(ff, FilterField):
            raise TypeError(
                f"filter_fields entries must be str or FilterField, got {type(ff).__name__}"
            )
        if ff.name not in schema_set:
            raise ValueError(
                f"filter field {ff.name!r} is not one of the data fields: {sorted(schema_set)}"
            )
        kind = ff.kind or _infer_kind(records, ff.name)
        if kind == CONTINUOUS:
            nums = [r[ff.name] for r in records if _is_number(r.get(ff.name))]
            if not nums:
                raise ValueError(
                    f"filter field {ff.name!r} declared continuous but has no numeric values"
                )
            specs.append(
                {
                    "name": ff.name,
                    "kind": CONTINUOUS,
                    "min": min(nums),
                    "max": max(nums),
                }
            )
        else:
            values = []
            seen: set[str] = set()
            for r in records:
                v = r.get(ff.name)
                key = json.dumps(v, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    values.append(v)
            specs.append({"name": ff.name, "kind": CATEGORICAL, "values": values})
    return specs


# --------------------------------------------------------------------------- #
# build the static browser
# --------------------------------------------------------------------------- #
TEMPLATE = Path(__file__).resolve().parent / "browser.html"


def build(
    data: Union[str, Path, Iterable[dict]],
    out_dir: Union[str, Path],
    *,
    filter_fields: Union[Sequence[FilterSpec], None] = None,
    title: Union[str, None] = None,
    strict: bool = True,
) -> Path:
    """Build a self-contained static browser for ``data`` into ``out_dir``.

    Writes three files: ``data.jsonl`` (normalized records), ``meta.json``
    (title, schema, resolved filter specs) and ``index.html`` (the single-page
    app). Returns the path to ``out_dir``.

    ``filter_fields`` defaults to ``None`` — no fields are filterable. Pass a
    list of field names, or :class:`FilterField` specs to force a kind.
    """
    records = load_records(data)
    schema = validate_schema(records, strict=strict)
    filters = build_filter_specs(records, filter_fields, schema)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with (out / "data.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, default=str))
            fh.write("\n")

    if title is None:
        title = Path(str(data)).stem if isinstance(data, (str, Path)) else "data"

    meta = {
        "title": title,
        "count": len(records),
        "fields": schema,
        "filters": filters,
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    (out / "index.html").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return out
