"""dotenv parsing and the Worker.spawn env merge order."""
from concierge import runtime
from concierge.records import Home, new_task


def test_parse_basic():
    env = runtime._parse_env_file(
        "\n".join([
            "# a comment",
            "",
            "FOO=bar",
            "export BAZ=qux",
            "QUOTED=\"has spaces\"",
            "SQUOTED='single'",
            "  export SPACED = trimmed ",
            "NOVALUE=",
            "junkline",
        ])
    )
    assert env["FOO"] == "bar"
    assert env["BAZ"] == "qux"
    assert env["QUOTED"] == "has spaces"
    assert env["SQUOTED"] == "single"
    assert env["SPACED"] == "trimmed"
    assert env["NOVALUE"] == ""
    assert "junkline" not in env
    assert "#" not in "".join(env.keys())


def test_env_file_default_missing_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.env under this HOME
    # absent key + no ~/.env → empty overrides, no crash
    assert runtime._env_overrides({}) == {}


def test_env_file_null_disables(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("FOO=bar")
    assert runtime._env_overrides({"env_file": None}) == {}


def test_env_file_missing_path_warns_not_crashes(tmp_path, capsys):
    missing = tmp_path / "nope.env"
    assert runtime._env_overrides({"env_file": str(missing)}) == {}
    assert "missing" in capsys.readouterr().out


def _spawn_capture(monkeypatch, home, task, cfg):
    captured = {}

    class FakeProc:
        pid = 4321

    def fake_popen(cmd, **kw):
        captured["env"] = kw["env"]
        return FakeProc()

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)
    runtime.Worker.spawn(home, task, "prompt", cfg)
    return captured["env"]


def test_spawn_merge_order(tmp_path, monkeypatch):
    home = Home(tmp_path / "home")
    task = new_task("t-x", "t", {"kind": "always"}, {"usd": 1, "wall_minutes": 1},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})

    dotenv = tmp_path / ".env"
    dotenv.write_text("\n".join([
        "SHARED=from_file",       # overrides os.environ
        "FILE_ONLY=yes",          # new key
        "CONCIERGE_HOME=/wrong",  # concierge var must still win
        "PYTHONPATH=/wrong",      # concierge var must still win
    ]))
    monkeypatch.setenv("SHARED", "from_os")
    monkeypatch.setenv("OS_ONLY", "present")

    env = _spawn_capture(monkeypatch, home, task, {"env_file": str(dotenv)})

    assert env["OS_ONLY"] == "present"          # inherited from os.environ
    assert env["SHARED"] == "from_file"         # env-file overrides os.environ
    assert env["FILE_ONLY"] == "yes"            # env-file adds new keys
    assert env["CONCIERGE_HOME"] == str(home.root)   # concierge wins last
    assert env["CONCIERGE_TASK_ID"] == "t-x"
    assert env["PYTHONPATH"].startswith(runtime.PKG_PARENT)  # concierge wins last
