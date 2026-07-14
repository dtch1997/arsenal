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
