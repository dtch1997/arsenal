"""The Codex backend (issue #60). No test invokes the real `codex` binary or
any network: a stub `codex` (tests/fixtures/codex_stub.py) replays a recorded
`codex exec --json` event stream and records the argv it was called with.

Covers: command construction (sandbox/access mapping, resume flag ordering,
--output-schema, -c MCP overrides, model default/override), event-stream
normalization to the frozen agent.jsonl schema, token→USD cost stamping,
structured-output passthrough, is_error on non-zero exit, the `backend` record
field default + legacy-record load, and that runtime picks the wrapper module
by backend (claude paths untouched)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from concierge import backends, runtime
from concierge.backends import codex
from concierge.records import Home, new_task, now_iso

FIXTURES = Path(__file__).parent / "fixtures"
STUB = FIXTURES / "codex_stub.py"

# a recorded codex-cli 0.147.0 `exec --json` stream (schema per issue #60 comment)
EVENTS = [
    {"type": "thread.started", "thread_id": "th-abc123", "model": "gpt-5.6-sol"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "planning"}},
    {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                        "command": "ls", "exit_code": 0, "aggregated_output": "a.py"}},
    {"type": "item.completed", "item": {"id": "i2", "type": "agent_message",
                                        "text": "Done: created a.py"}},
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 800,
                                         "output_tokens": 200}},
]


# -- command construction (pure) --

def _task(tmp_path, access="readwrite", model=None, backend="codex"):
    home = Home(tmp_path / "home")
    task = new_task("t-c", "codex task", {"kind": "always"},
                    {"usd": 10, "wall_minutes": 60},
                    {"repo": None, "base": "main", "branch": "b", "access": access},
                    model=model, backend=backend)
    home.save(task)
    log_dir = home.log_dir("t-c", 1)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "prompt.md").write_text("do the thing")
    return home, task, log_dir


def test_sandbox_mapping_readwrite(tmp_path):
    home, task, log_dir = _task(tmp_path, access="readwrite")
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    assert cmd[:3] == ["codex", "exec", "--json"]
    assert "--sandbox" in cmd and cmd[cmd.index("--sandbox") + 1] == "workspace-write"


def test_sandbox_mapping_readonly(tmp_path):
    home, task, log_dir = _task(tmp_path, access="readonly")
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"


def test_model_default_and_override(tmp_path):
    home, task, log_dir = _task(tmp_path, model=None)
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    assert cmd[cmd.index("-m") + 1] == codex.CODEX_MODEL_DEFAULT  # gpt-5.6-sol
    cmd = codex.build_command(home, task, {"codex_model": "gpt-x"}, None, None, 1, log_dir)
    assert cmd[cmd.index("-m") + 1] == "gpt-x"
    home2, task2, ld2 = _task(tmp_path / "b", model="pinned-model")
    cmd = codex.build_command(home2, task2, {"codex_model": "gpt-x"}, None, None, 1, ld2)
    assert cmd[cmd.index("-m") + 1] == "pinned-model"  # task model wins over config


def test_codex_bin_config(tmp_path):
    home, task, log_dir = _task(tmp_path)
    cmd = codex.build_command(home, task, {"codex_bin": "/opt/codex"}, None, None, 1, log_dir)
    assert cmd[0] == "/opt/codex"


def test_resume_flags_precede_subcommand(tmp_path):
    """The pinned gotcha: exec-level flags MUST come before `resume`."""
    home, task, log_dir = _task(tmp_path)
    cmd = codex.build_command(home, task, {}, "th-xyz", None, 1, log_dir)
    assert "resume" in cmd
    ri = cmd.index("resume")
    # every exec-level flag appears before `resume`; the thread id + prompt after
    for flag in ("--json", "--sandbox", "-m", "-C", "--skip-git-repo-check"):
        assert cmd.index(flag) < ri, f"{flag} must precede resume"
    assert cmd[ri + 1] == "th-xyz"
    assert cmd[ri + 2] == "do the thing"       # prompt is last, after the thread id
    assert cmd[-1] == "do the thing"


def test_fresh_run_has_no_resume(tmp_path):
    home, task, log_dir = _task(tmp_path)
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    assert "resume" not in cmd
    assert cmd[-1] == "do the thing"


def test_output_schema_flag(tmp_path):
    home, task, log_dir = _task(tmp_path)
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    (log_dir / "output_schema.json").write_text(json.dumps(schema))
    cmd = codex.build_command(home, task, {}, None, schema, 1, log_dir)
    assert "--output-schema" in cmd
    assert cmd[cmd.index("--output-schema") + 1] == str(log_dir / "output_schema.json")
    # absent when no schema
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    assert "--output-schema" not in cmd


def test_mcp_overrides_registered(tmp_path):
    home, task, log_dir = _task(tmp_path)
    cmd = codex.build_command(home, task, {}, None, None, 1, log_dir)
    joined = "\n".join(cmd)
    assert "mcp_servers.concierge.command=" in joined
    assert "mcp_servers.concierge.args=" in joined
    # args point at the stdio MCP server with home/tid/attempt
    args_val = next(c.split("=", 1)[1] for c in cmd if c.startswith("mcp_servers.concierge.args="))
    parsed = json.loads(args_val)
    assert parsed == ["-m", "concierge.mcp_stdio", str(home.root), "t-c", "1"]


# -- event-stream normalization (pure) --

def test_stream_normalizes_events():
    st = codex._Stream({}, output_schema=None)
    out = [st.feed(ev) for ev in EVENTS]
    out = [e for e in out if e]
    # thread.started -> system/init carrying session_id + backend
    assert out[0]["type"] == "system" and out[0]["subtype"] == "init"
    assert out[0]["backend"] == "codex" and out[0]["session_id"] == "th-abc123"
    # command_execution -> assistant tool_use; agent_message -> assistant text
    kinds = [e["message"]["content"][0]["type"] for e in out if e["type"] == "assistant"]
    assert kinds == ["tool_use", "text"]
    assert st.last_text == "Done: created a.py"
    assert st.usage == {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 200}
    assert st.turns == 1


def test_result_event_shape_and_cost():
    st = codex._Stream({"codex_cost_per_mtoken": {"input": 2.0, "output": 10.0}}, None)
    for ev in EVENTS:
        st.feed(ev)
    res = st.result_event(is_error=False)
    assert res["type"] == "result" and res["is_error"] is False
    assert res["session_id"] == "th-abc123"
    assert res["result"] == "Done: created a.py"
    # (1000*2 + 200*10) / 1e6 = 0.004
    assert abs(res["total_cost_usd"] - 0.004) < 1e-9
    assert res["structured_output"] is None  # no schema declared


def test_cost_scalar_rate():
    st = codex._Stream({"codex_cost_per_mtoken": 5.0}, None)
    st.usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert codex._usage_cost(st.usage, st.cfg) == 10.0


def test_cost_zero_without_config():
    assert codex._usage_cost({"input_tokens": 999, "output_tokens": 999}, {}) == 0.0


def test_structured_output_parsed_from_final_message():
    schema = {"type": "object"}
    st = codex._Stream({}, output_schema=schema)
    st.feed({"type": "thread.started", "thread_id": "th-1"})
    st.feed({"type": "item.completed",
             "item": {"type": "agent_message", "text": '{"status": "ok", "count": 3}'}})
    res = st.result_event(is_error=False)
    assert res["structured_output"] == {"status": "ok", "count": 3}


def test_error_event_marks_result_error():
    st = codex._Stream({}, None)
    st.feed({"type": "thread.started", "thread_id": "th-1"})
    st.feed({"type": "turn.failed", "error": "model refused"})
    res = st.result_event(is_error=False)   # stream-level error even if rc==0
    assert res["is_error"] is True
    assert res["subtype"] == "error"
    assert "model refused" in res["result"]


# -- end-to-end wrapper run (subprocess + stub codex) --

def _run_wrapper(home, tid, attempt, events, rc=0, resume=None):
    """Invoke the codex wrapper module as a detached subprocess (own session,
    exactly like runtime.Worker.spawn) with the stub codex on PATH via config.
    start_new_session is essential: the wrapper's die() kills its own process
    group, and we must not let that reach the pytest process."""
    events_file = home.root / "events.jsonl"
    events_file.write_text("".join(json.dumps(e) + "\n" for e in events))
    argv_dump = home.root / "codex_argv.txt"
    (home.root / "config.yaml").write_text(
        f"codex_bin: {STUB}\n"
        "codex_cost_per_mtoken:\n  input: 2.0\n  output: 10.0\n")
    os.chmod(STUB, 0o755)
    env = dict(os.environ)
    env.update(
        CONCIERGE_HOME=str(home.root),
        CODEX_STUB_EVENTS=str(events_file),
        CODEX_STUB_ARGV=str(argv_dump),
        CODEX_STUB_RC=str(rc),
        PYTHONPATH=runtime.PKG_PARENT + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    cmd = [sys.executable, "-m", "concierge.backends.codex", tid, str(attempt)]
    if resume:
        cmd += ["--resume", resume]
    subprocess.run(cmd, env=env, start_new_session=True, timeout=60,
                   capture_output=True)
    return argv_dump


def _agent_events(home, tid, attempt):
    p = home.log_dir(tid, attempt) / "agent.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_end_to_end_normalizes_to_agent_jsonl(tmp_path):
    home, task, log_dir = _task(tmp_path)
    home.workspace("t-c").mkdir(parents=True, exist_ok=True)
    _run_wrapper(home, "t-c", 1, EVENTS)

    evs = _agent_events(home, "t-c", 1)
    assert evs[0] == {"type": "system", "subtype": "init", "backend": "codex",
                      "session_id": "th-abc123", "model": "gpt-5.6-sol"}
    result = evs[-1]
    assert result["type"] == "result"
    assert result["session_id"] == "th-abc123"
    assert result["result"] == "Done: created a.py"
    assert result["is_error"] is False
    assert abs(result["total_cost_usd"] - 0.004) < 1e-9

    # Worker.poll (the frozen contract) reads it exactly like a claude log
    task["attempts"].append({"n": 1, "pid": 1, "started": now_iso(),
                             "session_id": None, "cost_usd": None,
                             "result": None, "log": "logs/t-c/attempt-1"})
    home.save(task)
    w = runtime.Worker(home, "t-c", 1, pid=1, started=now_iso())
    state = w.poll()
    assert state.ended is True
    assert state.session_id == "th-abc123"
    assert abs(state.cost_usd - 0.004) < 1e-9
    assert state.text == "Done: created a.py"
    assert state.error is None


def test_end_to_end_structured_output(tmp_path):
    home, task, log_dir = _task(tmp_path)
    home.workspace("t-c").mkdir(parents=True, exist_ok=True)
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    (log_dir / "output_schema.json").write_text(json.dumps(schema))
    events = [
        {"type": "thread.started", "thread_id": "th-s", "model": "gpt-5.6-sol"},
        {"type": "item.completed", "item": {"type": "agent_message",
                                            "text": '{"count": 7}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]
    argv_dump = _run_wrapper(home, "t-c", 1, events)

    result = _agent_events(home, "t-c", 1)[-1]
    assert result["structured_output"] == {"count": 7}
    # --output-schema was actually passed to codex
    argv = argv_dump.read_text().splitlines()
    assert "--output-schema" in argv


def test_end_to_end_nonzero_exit_is_error(tmp_path):
    home, task, log_dir = _task(tmp_path)
    home.workspace("t-c").mkdir(parents=True, exist_ok=True)
    events = [{"type": "thread.started", "thread_id": "th-e"}]
    _run_wrapper(home, "t-c", 1, events, rc=3)
    result = _agent_events(home, "t-c", 1)[-1]
    assert result["type"] == "result" and result["is_error"] is True


def test_end_to_end_resume_argv_ordering(tmp_path):
    home, task, log_dir = _task(tmp_path)
    home.workspace("t-c").mkdir(parents=True, exist_ok=True)
    argv_dump = _run_wrapper(home, "t-c", 1, EVENTS, resume="th-prev")
    argv = argv_dump.read_text().splitlines()
    ri = argv.index("resume")
    assert argv.index("--json") < ri            # exec flag before resume
    assert argv[ri + 1] == "th-prev"


# -- backend field: default, legacy load, module selection --

def test_new_task_backend_default_is_claude(tmp_path):
    task = new_task("t-x", "t", {"kind": "always"}, {"usd": 1, "wall_minutes": 1},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    assert task["backend"] == "claude"


def test_new_task_backend_explicit_codex(tmp_path):
    task = new_task("t-x", "t", {"kind": "always"}, {"usd": 1, "wall_minutes": 1},
                    {"repo": None, "base": "main", "branch": "b", "access": "readwrite"},
                    backend="codex")
    assert task["backend"] == "codex"


def test_module_for_default_and_legacy():
    assert backends.module_for(None) == "concierge.backends.claude"
    assert backends.module_for("claude") == "concierge.backends.claude"
    assert backends.module_for("codex") == "concierge.backends.codex"


def test_spawn_selects_module_by_backend(tmp_path, monkeypatch):
    """runtime.Worker.spawn picks the wrapper module by backend; a legacy record
    with no `backend` key still spawns the claude module."""
    home = Home(tmp_path / "home")
    captured = {}

    class FakeProc:
        pid = 111

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)

    codex_task = new_task("t-c", "t", {"kind": "always"}, {"usd": 1, "wall_minutes": 1},
                          {"repo": None, "base": "main", "branch": "b", "access": "readwrite"},
                          backend="codex")
    runtime.Worker.spawn(home, codex_task, "prompt", {})
    assert "concierge.backends.codex" in captured["cmd"]

    legacy = new_task("t-l", "t", {"kind": "always"}, {"usd": 1, "wall_minutes": 1},
                      {"repo": None, "base": "main", "branch": "b", "access": "readwrite"})
    del legacy["backend"]                       # simulate a pre-#60 record
    runtime.Worker.spawn(home, legacy, "prompt", {})
    assert "concierge.backends.claude" in captured["cmd"]
