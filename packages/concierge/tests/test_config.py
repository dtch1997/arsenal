"""load_config: read the file when present, {} when absent — never a silent
fallback for a present-but-unreadable config (a daemon on default limits
while config.yaml says otherwise looks healthy and enforces the wrong caps).
"""
import pytest

from concierge.records import Home, load_config


def _home(tmp_path):
    home = Home(tmp_path / "home")
    (home.root).mkdir(parents=True, exist_ok=True)
    return home


def test_missing_config_is_empty(tmp_path):
    assert load_config(_home(tmp_path)) == {}


def test_config_values_are_read(tmp_path):
    home = _home(tmp_path)
    (home.root / "config.yaml").write_text(
        "concurrency: 7\ndaily_usd_cap: 1000\n"
    )
    cfg = load_config(home)
    assert cfg["daily_usd_cap"] == 1000
    assert cfg["concurrency"] == 7


def test_unparseable_config_raises(tmp_path):
    home = _home(tmp_path)
    (home.root / "config.yaml").write_text("concurrency: [unclosed\n")
    with pytest.raises(Exception):
        load_config(home)


def test_default_backend_resolved_at_submit(tmp_path):
    # submit stamps the pool's default_backend onto the record; explicit
    # backend= wins; no config -> claude (records-level default)
    from concierge import Pool

    home = _home(tmp_path)
    (home.root / "config.yaml").write_text("default_backend: codex\n")
    pool = Pool(home.root)
    assert pool.get(pool.submit("x"))["backend"] == "codex"
    assert pool.get(pool.submit("x", backend="claude"))["backend"] == "claude"
    # kwarg overrides file config (Pool config precedence)
    pool2 = Pool(home.root, default_backend="claude")
    assert pool2.get(pool2.submit("x"))["backend"] == "claude"
    # absent everywhere -> claude
    home2 = _home(tmp_path / "h2")
    pool3 = Pool(home2.root)
    assert pool3.get(pool3.submit("x"))["backend"] == "claude"
