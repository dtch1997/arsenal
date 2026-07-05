"""agents — coding-agent instances as first-class steps.

An agent is just a step: `agent(flow, prompt, …)` spawns a headless coding agent
and returns a structured `AgentOutcome`, so it composes with the rest of the
engine — `best_of` for best-of-N agents (judge picks the best patch), `with_retry`
for retry-with-feedback (feed the test failures back), `reduce` to merge.

The work is done by a **backend** (an async `spec -> AgentOutcome`), behind a
seam so the core stays dependency-free:

  - `subprocess_backend` (default) — a zero-dep `claude -p --output-format json`
    runner; works standalone.
  - `flightdeck_backend()` — recommended: runs each agent as a flightdeck
    `AgentRun` (live stream-json capture → the dashboard, cost/tokens/resume).
    `flightdeck` is imported lazily, so it's an optional integration, not a dep.

Parallel agents that edit files must not clobber each other — pass
`isolation="worktree"` to run each in its own throwaway git worktree and capture
its diff.

    flow  = Flow("runs")
    patch = agent(flow, "fix the failing test in foo.py", isolation="worktree")
    best  = flow.map("solve", tasks, best_of(agent_fn, n=4, judge=pick_best_patch))
    await flow.run()
"""
from __future__ import annotations
import asyncio
import json
import tempfile
from dataclasses import dataclass, replace
from typing import Any

from .engine import current_monitor

DEFAULT_TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "TodoWrite")


@dataclass
class AgentOutcome:
    """The normalized result of an agent step — a stable schema downstream
    `check` / `judge` / `reduce` steps can act on regardless of backend."""
    ok: bool
    summary: str = ""                 # the agent's final message / verdict
    diff: str | None = None           # captured patch (when isolation="worktree")
    cost: float | None = None         # USD
    tokens: int | None = None
    session_id: str | None = None     # for `claude --resume <id>`
    name: str = ""
    raw: Any = None                   # the backend's native result


@dataclass
class AgentSpec:
    """What to run — handed to a backend."""
    prompt: str
    name: str = "agent"
    cwd: str = "."
    allowed_tools: tuple = DEFAULT_TOOLS
    permission_mode: str = "acceptEdits"
    model: str | None = None
    timeout: float | None = None
    extra_args: tuple = ()


# ---- backends ------------------------------------------------------------- #
async def subprocess_backend(spec: AgentSpec) -> AgentOutcome:
    """Zero-dep backend: `claude -p <prompt> --output-format json`. No live
    streaming (use `flightdeck_backend` for that), but standalone and testable."""
    argv = ["claude", "-p", spec.prompt, "--output-format", "json",
            "--allowed-tools", ",".join(spec.allowed_tools),
            "--permission-mode", spec.permission_mode]
    if spec.model:
        argv += ["--model", spec.model]
    argv += list(spec.extra_args)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=spec.cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return AgentOutcome(ok=False, summary=f"timed out after {spec.timeout}s",
                            name=spec.name)
    text = out.decode(errors="replace") if out else ""
    data = {}
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        pass
    m = current_monitor()
    if m is not None:
        m.set(status="done" if proc.returncode == 0 else "error",
              cost=data.get("total_cost_usd"))
    return AgentOutcome(
        ok=(proc.returncode == 0 and not data.get("is_error")),
        summary=data.get("result") or text[:500],
        cost=data.get("total_cost_usd"),
        session_id=data.get("session_id"),
        name=spec.name, raw=data or text)


def flightdeck_backend(**run_opts):
    """Recommended backend: run each agent as a flightdeck `AgentRun`, streaming
    its live state (status / last action / tokens / cost) to the step's monitor —
    so the fleet lights up the dashboard. `flightdeck` is imported lazily; pass
    extra `AgentRun` kwargs (e.g. `done_when=`, `alert=`) via `run_opts`."""
    async def backend(spec: AgentSpec) -> AgentOutcome:
        from flightdeck import AgentRun        # lazy: optional integration
        mon = current_monitor()

        def on_state(s):
            if mon is not None:
                mon.set(status=s.status, last_action=s.last_action,
                        turns=s.turns, tokens=s.tokens, cost=s.cost)

        res = await AgentRun(
            spec.prompt, name=spec.name, cwd=spec.cwd,
            allowed_tools=spec.allowed_tools, permission_mode=spec.permission_mode,
            model=spec.model, timeout=spec.timeout, on_state=on_state,
            **run_opts).go()
        return AgentOutcome(
            ok=res.ok, summary=(res.state.result or ""), cost=res.cost,
            tokens=res.tokens, session_id=res.session_id, name=res.name, raw=res)
    return backend


_default_backend = subprocess_backend


def set_default_backend(backend):
    """Set the backend used by `agent()` when none is passed (e.g. once at startup
    to `flightdeck_backend()`)."""
    global _default_backend
    _default_backend = backend


# ---- worktree isolation --------------------------------------------------- #
async def _git(*args, cwd):
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _with_worktree(base_cwd, run_in):
    """Run `run_in(worktree_path)` in a throwaway git worktree off HEAD, capture
    its diff onto the returned outcome, and remove the worktree."""
    rc, _, _ = await _git("rev-parse", "--show-toplevel", cwd=base_cwd)
    if rc != 0:
        raise RuntimeError("isolation='worktree' needs a git repo at cwd")
    wt = tempfile.mkdtemp(prefix="stagehand-agent-")
    await _git("worktree", "add", "--detach", wt, "HEAD", cwd=base_cwd)
    try:
        outcome = await run_in(wt)
        await _git("add", "-A", cwd=wt)
        _, diff, _ = await _git("diff", "--cached", cwd=wt)
        if isinstance(outcome, AgentOutcome):
            outcome.diff = diff
        return outcome
    finally:
        await _git("worktree", "remove", "--force", wt, cwd=base_cwd)


# ---- the step ------------------------------------------------------------- #
def agent(flow, prompt, *inputs, name=None, tools=DEFAULT_TOOLS, isolation=None,
          backend=None, permission_mode="acceptEdits", model=None, timeout=None,
          cwd=".", after=()):
    """A coding-agent step on `flow`. `prompt` is a string, or a callable built
    from upstream results: `agent(flow, lambda issue: f"fix {issue}",
    issue_handle)`. `inputs` (handles OK) feed that callable. Returns a one-task
    `Handle[AgentOutcome]`.

    `isolation="worktree"` runs the agent in its own git worktree and captures the
    diff (use it whenever agents run in parallel and edit files). `backend`
    defaults to `subprocess_backend`; pass `flightdeck_backend()` for live
    monitoring. For best-of-N / retry-with-feedback agents, wrap a step fn in
    `best_of` / `with_retry` and declare it with `flow.map` / `flow.spawn`.
    """
    be = backend or _default_backend
    nm = name or "agent"

    async def _run(*resolved):
        text = prompt(*resolved) if callable(prompt) else prompt
        spec = AgentSpec(prompt=text, name=nm, cwd=cwd, allowed_tools=tuple(tools),
                         permission_mode=permission_mode, model=model, timeout=timeout)
        if isolation == "worktree":
            return await _with_worktree(cwd, lambda wt: be(replace(spec, cwd=wt)))
        return await be(spec)

    out = flow.spawn(_run, inputs, name=nm, after=after)
    out.elem_type = AgentOutcome     # typed for downstream check()/judge
    return out
