"""``threads scan`` — incrementally summarize new/grown sessions.

Idempotent on ``(session_id, transcript_size)``: an unchanged transcript is
skipped with zero model calls; a grown one is re-summarized. Trivial sessions
(< 10 messages or < 5 min span) get a stub record and never call the model. A
per-run cap bounds model spend; hitting it prints a warning and flares.

``scan(check=True)`` is the offline gate hook — no model calls, no network.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, registry, spool, summarize
from .transcripts import Session, iter_transcript_paths, parse_transcript

# concurrent headless `claude -p` subprocesses during a backfill. Bounded so a
# 100+ call backfill finishes in minutes, not hours, without fork-bombing.
DEFAULT_CONCURRENCY = 8


@dataclass
class ScanResult:
    sessions_in_window: int = 0
    summarized: int = 0
    trivial: int = 0
    skipped_unchanged: int = 0
    model_calls: int = 0
    truncated: bool = False
    est_spend_usd: float = 0.0
    parse_warnings: int = 0
    errors: list[str] = field(default_factory=list)

    def report(self) -> str:
        return (
            f"scanned {self.sessions_in_window} session(s) in window: "
            f"{self.summarized} summarized ({self.model_calls} model call(s)), "
            f"{self.trivial} trivial-stub, {self.skipped_unchanged} unchanged. "
            f"est spend ${self.est_spend_usd:.3f}"
            + (f", parse warnings: {self.parse_warnings}" if self.parse_warnings else "")
            + (" — CALL CAP HIT (run truncated)" if self.truncated else "")
        )


def _flare_truncation(cap: int) -> None:
    try:
        import flare
        flare.send(
            f"scan hit the per-run cap of {cap} model calls; backfill incomplete "
            "(re-run with --all or a higher --max-calls)",
            sev="warn", source="threads",
        )
    except Exception:
        pass


def scan(*, days: int = config.DEFAULT_LOOKBACK_DAYS,
         max_calls: int = config.DEFAULT_MAX_CALLS, all_: bool = False,
         runner=summarize.default_runner, model: str = config.MODEL,
         concurrency: int = DEFAULT_CONCURRENCY,
         now: datetime | None = None) -> ScanResult:
    now = now or datetime.now(timezone.utc)
    config.ensure_spool()
    slugs = registry.memory_index_slugs()
    res = ScanResult()
    cap = None if all_ else max_calls
    manifest: dict[str, dict] = {}
    to_summarize: list[Session] = []

    for path in iter_transcript_paths(days, now=now):
        res.sessions_in_window += 1
        key = path.stem
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        # cheap idempotency: stat + the record's stored size, NO parse. This is
        # what keeps a no-change re-scan fast (<10s over a few hundred sessions).
        existing = spool.load_summary(key)
        if existing is not None and existing.get("transcript_size") == size:
            res.skipped_unchanged += 1
            manifest[key] = {"size": size,
                             "trivial": bool(existing.get("trivial")),
                             "path": str(path)}
            continue

        try:
            session = parse_transcript(path)
        except Exception as e:  # defensive: never let one file kill the sweep
            res.errors.append(f"{path.name}: {e}")
            continue
        res.parse_warnings += session.parse_warnings
        manifest[key] = {"size": session.size, "trivial": session.trivial,
                         "path": str(path)}

        if session.trivial:
            spool.write_summary(summarize.stub_record(session, now=now))
            res.trivial += 1
            continue

        if cap is not None and len(to_summarize) >= cap:
            res.truncated = True
            continue
        to_summarize.append(session)

    # model calls run concurrently — a backfill is I/O-bound on subprocesses.
    def _one(session: Session) -> dict | None:
        try:
            return summarize.summarize_session(
                session, slugs, runner=runner, model=model, now=now)
        except Exception as e:
            res.errors.append(f"summarize {session.session_id}: {e}")
            return None

    if to_summarize:
        workers = max(1, min(concurrency, len(to_summarize)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, s) for s in to_summarize]
            # write each summary the moment it completes (not in submit order)
            # so an interrupted backfill preserves maximal progress.
            for fut in as_completed(futures):
                record = fut.result()
                if record is None:
                    continue
                spool.write_summary(record)
                res.summarized += 1
                res.model_calls += 1
                res.est_spend_usd += record.get("cost_usd") or config.EST_USD_PER_CALL

    if res.truncated and cap is not None:
        _flare_truncation(cap)

    spool.write_state({
        "last_scan": now.isoformat(),
        "lookback_days": days,
        "sessions_in_window": res.sessions_in_window,
        "summarized": res.summarized,
        "trivial": res.trivial,
        "skipped_unchanged": res.skipped_unchanged,
        "model_calls": res.model_calls,
        "truncated": res.truncated,
        "est_spend_usd": round(res.est_spend_usd, 4),
        "parse_warnings": res.parse_warnings,
        "manifest": manifest,
    })
    return res


def scan_check(*, days: int = config.DEFAULT_LOOKBACK_DAYS,
               now: datetime | None = None) -> tuple[bool, str]:
    """Offline gate (stat-only, no parse, no model): a scan has completed over
    the window and every in-window session is covered.

    "Covered" is tolerant of the fact that this runs on a *live* box where
    active transcripts (including the worker's own) keep growing: a session that
    already has a **model** summary counts as covered even if it has since grown
    a few lines — the summary exists; re-scanning only refreshes it. Only a
    session with no record at all, or a still-*stub* record whose transcript
    grew (a trivial session that may have crossed into non-trivial), fails."""
    now = now or datetime.now(timezone.utc)
    state = spool.load_state()
    if not state:
        return False, "no scan has run (state.json missing)"
    if int(state.get("lookback_days", 0)) < days:
        return False, (
            f"last scan covered {state.get('lookback_days')}d < requested {days}d")

    problems: list[str] = []
    checked = covered = drifted = 0
    for path in iter_transcript_paths(days, now=now):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        checked += 1
        rec = spool.load_summary(path.stem)
        if rec is None:
            problems.append(f"new session not summarized: {path.name}")
            continue
        if rec.get("method") == "model":
            covered += 1
            if rec.get("transcript_size") != size:
                drifted += 1  # grown since scan; summary present, refresh-only
            continue
        # a stub (trivial) record: fine only while the transcript is unchanged
        if rec.get("transcript_size") != size:
            problems.append(f"stub session grew (may now be non-trivial): {path.name}")
            continue
        covered += 1

    if problems:
        head = (f"scan --check FAIL: {len(problems)} problem(s) over "
                f"{checked} session(s)")
        return False, head + "\n  - " + "\n  - ".join(problems[:20])
    return True, (
        f"scan --check OK: {covered}/{checked} in-window session(s) covered "
        f"({drifted} grown since scan, refresh-only) over a {days}d window")
