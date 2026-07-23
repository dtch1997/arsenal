"""Trees and leaves: workers calling up new workers within the same pool.

Delegation is queue-insertion with parentage, never pool-creation. A child is
an ordinary task record in the same CONCIERGE_HOME, dispatched by the same
daemon under the same concurrency slot cap — when the pool is full, children
queue like every other task. The single-writer rule holds: a delegating worker
creates NEW child records (exactly what Pool.submit does from any other
process) but never writes its own daemon-owned record; everything derived
about a parent's tree — its children, its delegated budget — is computed by
scanning child records for `parent == tid`.

The parent parks, it never waits in-session: after delegating, the parent
calls signal_waiting on the probe_children probe and releases its slot, so its
children can flow through the slots it vacated. A parent that stayed `running`
while its children queued could wedge the pool at `concurrency` (four parents
holding all four slots, waiting on children that can never dispatch).

Failure semantics: a failed child never cascades. The parent is resumed with
every child's outcome (the probe fires on ALL-terminal, not all-done) and
decides — retry with a sharper spec, absorb the work, or signal_blocked up to
the human. The parent's own gate remains its definition of done; children
passing their gates proves nothing about the parent.
"""
from __future__ import annotations

from .gates import Always, Gate
from .records import TERMINAL, new_id, new_task

# root → planner → leaves; deeper trees mean specs written by specs written by
# specs, which is where swarm quality falls off a cliff
MAX_DEPTH_DEFAULT = 2
CHILD_BUDGET_USD_DEFAULT = 10.0
CHILD_BUDGET_MINUTES_DEFAULT = 120.0


class DelegationError(RuntimeError):
    """Refusal with a worker-legible reason (returned as tool output, never raised
    through the session)."""


def children(home, tid: str) -> list[dict]:
    return [t for t in home.tasks() if t.get("parent") == tid]


def delegate_child(home, parent: dict, cfg: dict, *, title: str, spec: str,
                   gate: dict | None = None, budget_usd: float | None = None,
                   budget_minutes: float | None = None, model: str | None = None,
                   base: str | None = None) -> dict:
    """Create one child task record in the parent's pool; returns the saved record.

    Enforces the two recursion rails at the only choke point workers have:
      - depth cap (config `max_depth`, default 2);
      - budget carve — child USD budgets come out of the parent's remaining
        envelope (budget minus own spend minus already-delegated), so a tree
        can never mint money beyond its root's budget. daily_usd_cap remains
        the global backstop.
    """
    depth = parent.get("depth", 0) + 1
    max_depth = cfg.get("max_depth", MAX_DEPTH_DEFAULT)
    if depth > max_depth:
        raise DelegationError(
            f"depth cap: this task is at depth {depth - 1} and the pool's max_depth "
            f"is {max_depth} — do the work yourself instead of delegating")

    spent = sum(a.get("cost_usd") or 0 for a in parent["attempts"])
    delegated = sum(c["budget"]["usd"] for c in children(home, parent["id"]))
    remaining = parent["budget"]["usd"] - spent - delegated
    usd = budget_usd if budget_usd is not None else min(CHILD_BUDGET_USD_DEFAULT, remaining)
    if usd <= 0 or usd > remaining:
        raise DelegationError(
            f"budget carve: ${usd:.2f} requested but only ${max(remaining, 0):.2f} of your "
            f"${parent['budget']['usd']:.2f} budget remains (spent ${spent:.2f}, already "
            f"delegated ${delegated:.2f}) — narrow the subtask or absorb the work")

    # validate a worker-supplied serialized gate before it enters a record the
    # reconciler will trust; None means Always, same default as Pool.submit
    try:
        gate_json = Gate.from_json(gate).to_json() if gate else Always().to_json()
    except (ValueError, TypeError, KeyError) as e:
        raise DelegationError(f"bad gate {gate!r}: {e}") from None

    tid = new_id()
    pw = parent["workspace"]
    child = new_task(
        tid,
        title=title,
        gate=gate_json,
        budget={"usd": usd, "wall_minutes": budget_minutes or CHILD_BUDGET_MINUTES_DEFAULT},
        # children inherit the parent's repo and access; base defaults to the
        # parent's own base (usually main) — pass base=<your pushed branch> for
        # children that build on the parent's work
        workspace={"repo": pw.get("repo"), "base": base or pw.get("base", "main"),
                   "branch": f"pool/{tid}", "access": pw.get("access", "readwrite")},
        # depth bonus: a started tree drains before newly submitted roots
        # dispatch, so parked parents don't starve behind fresh work
        priority=parent.get("priority", 0) + 1,
        notify=parent.get("notify"),
        parent=parent["id"],
        depth=depth,
        # inherit-by-default: the parent chooses a cheaper leaf model explicitly
        model=model or parent.get("model"),
    )
    home.spec_path(tid).write_text(spec)
    home.save(child)
    return child


def probe_children(home, tid: str, out=print) -> int:
    """Wake probe for a parked parent: exit 0 iff the task has children and ALL
    are terminal (done/failed/cancelled — the parent handles failures itself).

    Printed summary rides back to the parent inside the wake message
    (_maybe_wake includes the probe's output tail), so keep it compact.
    """
    kids = children(home, tid)
    if not kids:
        out(f"{tid} has no children — nothing to wait on")
        return 2
    for c in kids:
        detail = (c.get("status_detail") or "").replace("\n", " ")[:80]
        out(f"{c['id']} [{c['status']}] {c['title']}: {detail}")
    return 0 if all(c["status"] in TERMINAL for c in kids) else 1
