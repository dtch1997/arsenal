# threads

The **bottom-up activity spine**. Jarvis's memory registry (`~/jarvis-memory`)
is *self-reported* top-down state: a thread's line says whatever the last
session wrote, and threads that end abruptly go stale silently. `threads` adds
the missing *observed* layer — it reads every Claude session transcript, distils
each into a durable summary, weaves those summaries onto the existing registry
as **threads**, and renders "which threads actually got worked on, which are
dormant, and what sessions belong to no known thread."

```
~/.claude/projects/**/*.jsonl  ──scan──▶  ~/.threads/summaries/*.json
                                              │
              ~/jarvis-memory (registry) ──weave──▶  assignments.jsonl + candidates/
                                              │
                                          serve / render  ──▶  dashboard
```

No second registry: a **thread** is a memory-stub slug. `threads` never writes
into `~/jarvis-memory` — promoting a candidate thread is a human/consolidation
job, and deletion is Daniel's veto.

## Storage — the `~/.threads/` spool

Summaries are *derived but durable*: transcripts age out of `~/.claude`, the
spool does not.

| path | holds |
|---|---|
| `summaries/<session_id>.json` | one summary record per session (title, 3–6 sentence summary, artifacts, status signals, candidate slugs, keywords, deterministic match hints) |
| `assignments.jsonl` | `{session_id, slug\|null, method, confidence}` per session |
| `candidates/<name>.md` | agent-drafted candidate threads for unfiled clusters |
| `state.json` | scan cursor: lookback, per-run stats, and a size manifest |

## Commands

```bash
threads scan     # summarize new/grown sessions (incremental; idempotent)
threads weave    # assign summaries to threads; draft candidates for the unfiled
threads render   # print the dashboard as a markdown digest (stdout)
threads status   # one-line activity + gate summary
threads serve    # serve the dashboard through the lobby hub, re-rendering every 60s
```

### `threads scan`

Incremental sweep of `~/.claude/projects/**/*.jsonl` (default lookback 30 days,
`--days`). Keyed on `(session_id, transcript_size)`: an unchanged transcript is
skipped with **zero** model calls; a grown one is re-summarized. Trivial
sessions (< 10 messages or < 5 min span) get a stub record from the first user
message — no model call. Everything else gets **one** headless
`claude -p` call (`claude-haiku-4-5-20251001`, JSON-schema-shaped output) over a
downsampled transcript view (user turns + assistant text + tool *names*; tool
I/O elided; capped at ~50k chars head+tail). Malformed transcript lines are
skipped and tallied, never fatal.

Cost guardrails: a per-run cap (`--max-calls`, default 100; `--all` lifts it for
backfill). Hitting the cap prints a warning and fires `flare --sev warn
--source threads`. Per-run call count + estimated spend land in `state.json`.

### `threads weave`

Deterministic passes first (from the hints stored at scan time — no model, no
transcript re-read), in priority order:

1. **concierge** — a `concierge-home/workspaces/<tid>` cwd → the task JSON →
   its repo/title/spec mapped to a slug, else the `unmatched-concierge` review
   bucket.
2. **stub / goals / branch** — a memory-stub or `goals/<slug>.md` edited, or a
   git branch, mapped to a slug.
3. **repo** — the dominant touched/referenced repo (transcript file paths, cwd,
   and `owner/repo` references) mapped to a slug via exact match, stub mention,
   then normalized substring.
4. **model-validated fallback** — the summarizer's `candidate_slugs`, accepted
   only if the slug exists in the registry.

Unmatched → the unfiled inbox. Then **one** clustering call over the unfiled
summaries drafts candidate threads (≥ 2 members each; singletons stay in the
inbox) into `candidates/`. Prints an overall + per-method match report.

### The dashboard (`serve` / `render`)

`threads serve` registers with the lobby hub the way databrowser/desk do →
`https://<hub>…/a/threads/`, falling back to a plain localhost server (with a
notice) if lobby is down; it re-renders on an interval (60 s default). Views:

1. **Thread table** — one row per slug with ≥ 1 assigned session: last activity,
   session count, a 30-day activity sparkline, latest title, and a **dormancy
   flag** (default 14 days, suppressed when the MEMORY.md line already reads
   closed/retired/complete).
2. **Thread drill-down** — that thread's summaries newest-first with artifact
   links (PR/branch/URL).
3. **Unfiled inbox** — unmatched sessions, newest first.
4. **Candidate threads** — the drafted clusters.

`threads render` emits the same content as a markdown digest to stdout.

## `--check` gate hooks (cheap, offline, no model calls)

- `threads scan --check` — exit 0 iff the spool covers the configured lookback,
  every non-trivial transcript in the window has a model summary, and an
  incremental re-scan right now would be a no-op. Prints what it checked.
- `threads weave --check` — exit 0 iff assignments cover every summarized
  session, the deterministic-match rate over non-trivial, non-concierge
  sessions is **≥ 70 %**, and concierge-workspace sessions are 100 % resolved
  (to a slug or the review bucket). Prints the actual rates.

## Install

```bash
pip install -e .          # from this directory (uv workspace: uv sync --all-packages)
```

## Recommended cron (not installed for you)

`threads` installs **no** crontab. Add this yourself (`crontab -e`) once happy:

```cron
# daily 07:00: refresh summaries and re-weave onto the registry
0 7 * * * cd $HOME && threads scan && threads weave >> $HOME/.threads/cron.log 2>&1
```

Keep `threads serve` alive across logout with tmux:

```bash
tmux new-session -d -s threads-dashboard "threads serve"
```

## Env overrides (mostly for tests / smoke runs)

- `THREADS_HOME` — base dir instead of `~` (`~/.threads` resolves under it).
- `THREADS_PROJECTS_DIR` — the `~/.claude/projects` transcript tree.
- `THREADS_MEMORY_DIR` — the `~/jarvis-memory` registry.
- `THREADS_CONCIERGE_HOME` — the `~/concierge-home` root.

Real transcript content is sensitive: summaries stay in `~/.threads` and are
never committed or logged.
