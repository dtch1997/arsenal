import json
import subprocess
import sys
from pathlib import Path

import pytest

from databrowser import (
    CATEGORICAL,
    CONTINUOUS,
    FilterField,
    SchemaError,
    build,
    load_records,
    validate_schema,
)
from databrowser.core import build_filter_specs

RECORDS = [
    {"model": "a", "split": "train", "score": 0.1, "text": "hello"},
    {"model": "b", "split": "test", "score": 0.9, "text": "world"},
    {"model": "a", "split": "test", "score": 0.5, "text": "foo"},
]


def test_load_jsonl(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
    assert load_records(p) == RECORDS


def test_load_json_array(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps(RECORDS))
    assert load_records(p) == RECORDS


def test_load_blank_lines_skipped(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps(RECORDS[0]) + "\n\n" + json.dumps(RECORDS[1]) + "\n")
    assert load_records(p) == RECORDS[:2]


def test_non_object_record_rejected():
    with pytest.raises(SchemaError):
        load_records([{"a": 1}, [1, 2, 3]])


def test_validate_schema_ok():
    assert validate_schema(RECORDS) == ["model", "split", "score", "text"]


def test_validate_schema_strict_mismatch():
    bad = RECORDS + [{"model": "c"}]
    with pytest.raises(SchemaError) as e:
        validate_schema(bad)
    assert "missing" in str(e.value)


def test_validate_schema_nonstrict_union():
    bad = [{"a": 1}, {"a": 2, "b": 3}]
    assert validate_schema(bad, strict=False) == ["a", "b"]


def test_infer_categorical_and_continuous():
    specs = build_filter_specs(RECORDS, ["model", "score"], ["model", "split", "score", "text"])
    by_name = {s["name"]: s for s in specs}
    assert by_name["model"]["kind"] == CATEGORICAL
    assert by_name["model"]["values"] == ["a", "b"]
    assert by_name["score"]["kind"] == CONTINUOUS
    assert by_name["score"]["min"] == 0.1
    assert by_name["score"]["max"] == 0.9


def test_bool_is_categorical_not_continuous():
    recs = [{"flag": True}, {"flag": False}]
    specs = build_filter_specs(recs, ["flag"], ["flag"])
    assert specs[0]["kind"] == CATEGORICAL


def test_force_kind_override():
    # score forced categorical
    specs = build_filter_specs(RECORDS, [FilterField("score", CATEGORICAL)], ["model", "split", "score", "text"])
    assert specs[0]["kind"] == CATEGORICAL
    assert set(specs[0]["values"]) == {0.1, 0.9, 0.5}


def test_continuous_with_no_numbers_errors():
    with pytest.raises(ValueError):
        build_filter_specs(RECORDS, [FilterField("model", CONTINUOUS)], ["model", "split", "score", "text"])


def test_unknown_filter_field_errors():
    with pytest.raises(ValueError):
        build_filter_specs(RECORDS, ["nope"], ["model", "split", "score", "text"])


def test_no_filter_fields_default():
    assert build_filter_specs(RECORDS, None, ["model"]) == []


def test_build_writes_files(tmp_path):
    out = build(RECORDS, tmp_path / "out", filter_fields=["model", "score"], title="demo")
    assert (out / "data.jsonl").exists()
    assert (out / "index.html").exists()
    meta = json.loads((out / "meta.json").read_text())
    assert meta["title"] == "demo"
    assert meta["count"] == 3
    assert meta["fields"] == ["model", "split", "score", "text"]
    assert {f["name"] for f in meta["filters"]} == {"model", "score"}
    # data.jsonl round-trips
    lines = [json.loads(l) for l in (out / "data.jsonl").read_text().splitlines() if l.strip()]
    assert lines == RECORDS


def test_build_default_no_filters(tmp_path):
    out = build(RECORDS, tmp_path / "out")
    meta = json.loads((out / "meta.json").read_text())
    assert meta["filters"] == []


def test_cli_build(tmp_path):
    src = tmp_path / "d.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
    out = tmp_path / "site"
    r = subprocess.run(
        [sys.executable, "-m", "databrowser.cli", "build", str(src),
         "--out", str(out), "--filter", "model,score"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (out / "index.html").exists()
    meta = json.loads((out / "meta.json").read_text())
    assert {f["name"] for f in meta["filters"]} == {"model", "score"}
