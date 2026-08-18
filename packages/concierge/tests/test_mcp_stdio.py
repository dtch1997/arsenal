"""The stdio MCP server (concierge.mcp_stdio) for non-SDK backends, and the
shared signal logic in concierge.signals that both it and the Claude in-process
tools call. v1 codex workers are leaves only: `delegate` is NOT exposed."""
import asyncio
import json

from concierge import signals
from concierge.mcp_stdio import build_server
from concierge.records import Home


def test_server_exposes_only_blocked_and_waiting(tmp_path):
    home = Home(tmp_path / "home")
    server = build_server(home, "t-x", attempt=1, cfg={})
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"signal_blocked", "signal_waiting"}
    assert "delegate" not in names          # leaves only in v1


def test_signal_blocked_tool_posts_to_mailbox(tmp_path):
    home = Home(tmp_path / "home")
    server = build_server(home, "t-x", attempt=1, cfg={})
    asyncio.run(server.call_tool("signal_blocked", {"question": "which seed?"}))
    msgs = home.messages("t-x")
    assert msgs[-1]["from"] == "worker"
    assert msgs[-1]["text"] == "which seed?"
    assert msgs[-1]["via"] == "tool"


def test_signal_waiting_tool_writes_current_attempt_sidecar(tmp_path):
    home = Home(tmp_path / "home")
    server = build_server(home, "t-x", attempt=3, cfg={})
    asyncio.run(server.call_tool(
        "signal_waiting", {"until_shell": "test -f DONE", "note": "pod pipeline"}))
    sc = json.loads(home.wait_path("t-x").read_text())
    assert sc["attempt"] == 3
    assert sc["until_shell"] == "test -f DONE"
    assert sc["note"] == "pod pipeline"
    assert sc["timeout_minutes"] == signals.WAIT_TIMEOUT_MINUTES  # config default
    assert not list((home.root / "tasks").glob("*.tmp"))          # atomic write


def test_signal_waiting_honors_config_and_explicit_timeout(tmp_path):
    home = Home(tmp_path / "home")
    server = build_server(home, "t-x", attempt=1, cfg={"wait_timeout_minutes": 42})
    asyncio.run(server.call_tool("signal_waiting", {"until_shell": "true", "note": "n"}))
    assert json.loads(home.wait_path("t-x").read_text())["timeout_minutes"] == 42
    asyncio.run(server.call_tool(
        "signal_waiting", {"until_shell": "true", "note": "n", "timeout_minutes": 9}))
    assert json.loads(home.wait_path("t-x").read_text())["timeout_minutes"] == 9


# -- the shared signals layer (one implementation for every backend) --

def test_signals_write_waiting_matches_claude_sidecar_shape(tmp_path):
    home = Home(tmp_path / "home")
    sc = signals.write_waiting(home, "t-x", until_shell="true", note="n", attempt=2)
    assert sc["attempt"] == 2 and sc["timeout_minutes"] == signals.WAIT_TIMEOUT_MINUTES
    assert set(sc) == {"until_shell", "note", "timeout_minutes", "requested_at", "attempt"}


def test_claude_backend_reuses_signals(tmp_path, monkeypatch):
    """The Claude in-process tool and the MCP server must go through the SAME
    signals functions (factored, not duplicated)."""
    from concierge import worker
    home = Home(tmp_path / "home")
    calls = {}
    monkeypatch.setattr(signals, "write_waiting",
                        lambda *a, **k: calls.setdefault("waiting", (a, k)))
    monkeypatch.setattr(signals, "post_blocked",
                        lambda *a, **k: calls.setdefault("blocked", (a, k)))
    asyncio.run(worker._waiting_tool(home, "t-x", {}, attempt=1)
                .handler({"until_shell": "true", "note": "n"}))
    asyncio.run(worker._blocked_tool(home, "t-x").handler({"question": "q"}))
    assert "waiting" in calls and "blocked" in calls
