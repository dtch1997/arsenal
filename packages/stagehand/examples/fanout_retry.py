"""Fan-out and retry on the stagehand API — the two per-unit combinators that wrap
a unit-fn and drop straight into `stage`, with the compute faked so it runs anywhere
in a couple of seconds.

    best_of(fn, n, score=...)   -- run N independent attempts per unit, keep the best
    with_retry(fn, check=...)   -- retry a unit with feedback until it passes

Both run under `monitor`, so the losing/superseded attempts show up red on the live
dashboard next to the winner. Run it and open the printed status.html:

    uv run python examples/fanout_retry.py
"""
from __future__ import annotations
import asyncio
import json
import random
from pathlib import Path

from stagehand import stage, best_of, with_retry, live_dashboard, monitor


# ---- fan-out: 4 sampled attempts per prompt, keep the highest reward ------- #
async def sample_one(prompt: str, runs_dir: Path, *, attempt: int = 0):
    """One sampled attempt. `attempt` varies the seed so the tries actually differ."""
    rd = runs_dir / prompt
    path = rd / f"sample{attempt}.progress.json"
    with monitor(f"{prompt} · sample{attempt}", 1, path,
                 parent=prompt, meta={"phase": "sample"}, min_interval=0) as m:
        await asyncio.sleep(0.02)
        reward = round(random.Random(f"{prompt}-{attempt}").uniform(0, 1), 2)
        m.set(reward=reward)
        m.update()
    return {"prompt": prompt, "attempt": attempt, "reward": reward, "path": path}


# ---- retry-with-feedback: keep drafting until the draft is long enough ----- #
async def draft_one(prompt: str, runs_dir: Path, *, attempt: int = 0, feedback=None):
    """A 'draft' that gets longer each retry as it incorporates the feedback."""
    rd = runs_dir / prompt
    path = rd / f"draft{attempt}.progress.json"
    with monitor(f"{prompt} · draft{attempt}", 1, path,
                 parent=prompt, meta={"phase": "draft"}, min_interval=0) as m:
        await asyncio.sleep(0.02)
        length = 3 + attempt * 4                  # pretend the feedback helped
        if feedback:
            m.set(took_feedback=str(feedback))
        m.set(length=length)
        m.update()
    return {"prompt": prompt, "attempt": attempt, "length": length, "path": path}


def long_enough(draft):
    ok = draft["length"] >= 10
    return (ok, [] if ok else [f"too short ({draft['length']} < 10)"])


async def main():
    runs_dir = Path("runs-fanout-retry")
    prompts = ["alpha", "bravo", "charlie"]

    async with live_dashboard(runs_dir, title="fan-out + retry") as status_html:
        # FAN-OUT: 4 attempts per prompt, keep the best reward, mark losers red
        best = await stage(
            prompts,
            best_of(lambda p, *, attempt=0: sample_one(p, runs_dir, attempt=attempt),
                    n=4, score=lambda r: r["reward"],
                    monitor_path=lambda r: r["path"]),
            concurrency=2)
        print("[best_of] " + ", ".join(
            f"{b['prompt']}=#{b['attempt']}({b['reward']})" for b in best))

        # RETRY: redraft each prompt with feedback until it's long enough
        drafts = await stage(
            prompts,
            with_retry(
                lambda p, *, attempt=0, feedback=None:
                    draft_one(p, runs_dir, attempt=attempt, feedback=feedback),
                check=long_enough, max_attempts=4,
                monitor_path=lambda r: r["path"]),
            concurrency=3)
        print("[with_retry] " + ", ".join(
            f"{d['prompt']}=len{d['length']}@try{d['attempt']}" for d in drafts))

        manifest = {"best": best, "drafts": drafts}
        (runs_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print(f"done — open {status_html} ; manifest at {runs_dir / 'manifest.json'}")


if __name__ == "__main__":
    asyncio.run(main())
