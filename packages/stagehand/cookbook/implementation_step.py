"""Recipe: the IMPLEMENTATION step — an agent builds a feature, a **review** gates
it, it **retries** with the review's feedback up to N times, then the approved
change is persisted as a **PR**.

    body         agent implements the change (in a worktree)        agent(isolation="worktree")
    criterion    a review step approves it (tests + a reviewer)      checks / a judge agent
    reliability  retry, feeding the review's findings back, ≤N times  with_retry
    persist      commit + open a PR; the pointer is the PR URL        a persist seam (gh)

The review runs once per attempt *inside* the implement body and embeds its verdict,
so `with_retry` drives the loop on that verdict (no re-reviewing) and the final gate
reads it (no re-reviewing). The change is PR'd only if the review approved it.

Three seams — swap the fakes for your stack:
  - `implement(spec, attempt, feedback)` — the coding agent (real: `agent()` with a
    worktree backend; the feedback is woven into its prompt).
  - `review(result)` — tests + a code-review agent, returning `(ok, findings)`
    (e.g. `tests_pass(cwd=wt) & code_reviewer(diff)`).
  - `open_pr(result)` — commit + push + `gh pr create`, returning the PR URL.

    uv run python cookbook/implementation_step.py
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from pathlib import Path

from stagehand import Flow, with_retry, live_dashboard
from stagehand.checks import CheckResult, ok, fail

LOCAL = Path("runs-cookbook-impl")
MAX_ATTEMPTS = 3


@dataclass
class Spec:
    slug: str
    task: str


# ---- seams (swap for your stack) ------------------------------------------ #
async def implement(spec: Spec, *, attempt=0, feedback=None):
    """YOUR coding agent. Real: `await backend(AgentSpec(prompt=build(spec, feedback),
    cwd=worktree))` → an AgentOutcome whose `.diff` is the change. Faked: the first
    attempt is 'buggy', later attempts incorporate the feedback."""
    await asyncio.sleep(0.02)
    fixed = attempt >= 1 and "impossible" not in spec.task   # never satisfies an impossible task
    note = f" (addressed: {feedback})" if feedback else ""
    return {"diff": f"# {spec.slug} impl, attempt {attempt}{note}", "_fixed": fixed}


def review(result) -> CheckResult:
    """YOUR review gate — e.g. `tests_pass(cwd=wt) & code_reviewer(result['diff'])`.
    Returns (ok, findings); the findings become the agent's next-attempt feedback.
    Faked: approve once the implementation is 'fixed'."""
    return ok() if result.get("_fixed") else fail("review: missing an edge-case test")


async def open_pr(result) -> dict:
    """YOUR persist. Real: commit the diff to a branch, push, `gh pr create` → URL.
    Faked: a deterministic URL."""
    await asyncio.sleep(0.01)
    return {"pr": f"https://github.com/you/repo/pull/{abs(hash(result['diff'])) % 900 + 100}"}


# ---- the recipe (a node over a list of feature-specs) --------------------- #
def implement_reviewed(flow, name, specs, *, max_attempts=MAX_ATTEMPTS):
    async def body(s, *, attempt=0, feedback=None):
        result = await implement(s, attempt=attempt, feedback=feedback)
        verdict = review(result)                      # review once per attempt...
        result["approved"] = bool(verdict.ok)
        result["findings"] = verdict.issues          # ...embed the verdict
        return result

    # with_retry loops on the embedded verdict, feeding findings back, ≤ max_attempts
    impl = flow.map(name, specs,
                    with_retry(body, max_attempts=max_attempts,
                               check=lambda r: (r["approved"], r["findings"]),
                               feedback=lambda r, findings: "; ".join(findings)))
    # final gate: PR only what the review approved (reads the embedded verdict — no re-review)
    approved = flow.filter(f"{name}-review", impl,
                           lambda r: (r["approved"], ["review never passed"]))
    return flow.map(f"{name}-pr", approved, open_pr)


async def main():
    specs = [Spec("add-cache", "Add an LRU cache to the loader"),
             Spec("add-retry", "Add retry/backoff to the HTTP client"),
             Spec("squaring", "impossible to satisfy the review")]   # never approved -> dropped

    flow = Flow(LOCAL, title="implementation", concurrency=2)
    prs = implement_reviewed(flow, "implement", specs)
    # …and implementation feeds run steps: each run depends on its PR's code.
    flow.map("downstream", prs,
             lambda p: _noop(f"would run experiments against {p['pr']}"))

    async with live_dashboard(LOCAL, title="implementation") as status_html:
        state = await flow.run()

    for r in prs.results():
        print(f"approved & PR'd: {r['pr']}")
    print(f"{len(prs.results())}/{len(specs)} approved within {MAX_ATTEMPTS} attempts "
          f"(the rest dropped by the review gate)")
    print(f"{state.done} ok, {state.failed} failed, {state.skipped} skipped; open {status_html}")


async def _noop(x):
    return x


if __name__ == "__main__":
    asyncio.run(main())
