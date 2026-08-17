# threads

**Know what you — and your AI agents — actually worked on.**

If you run a lot of Claude Code sessions, activity quickly outruns memory:
projects go quiet without anyone deciding to stop, promising starts get
forgotten, and "what happened this month?" has no reliable answer. Any notes
you keep say whatever was last written, which is not the same thing as what
was done.

`threads` answers from the ground truth instead. It reads your Claude Code
session transcripts, distills each session into a short durable summary, and
groups related sessions into **threads** — one per project — so you can see
where effort is actually going, what's gone dormant, and what fell through
the cracks.

## What it lets you accomplish

- **See your real activity at a glance.** A dashboard of every thread,
  ranked by a tunable relevance score (volume × recency), with 30-day
  sparklines, last-touched dates, sorting, and filters.
- **Catch dropped work before it's lost.** Threads that go quiet get a
  dormancy flag; sessions that match no known project land in an *unfiled
  inbox* instead of vanishing.
- **Park work and pick it back up cheaply.** `threads note` saves a context
  dump (state, next steps, pointers) onto a thread as you step away;
  `threads pickup` prints everything needed to resume — your parked notes
  plus what the transcripts show happened since.
- **See structure you didn't plan.** Clusters of related unfiled sessions
  become auto-drafted *candidate threads*; related threads roll up into
  *programs* and *goals* in a hierarchy view with aggregated stats. Keep
  what's right, delete what isn't — everything auto-drafted is yours to veto.
- **Browse it as a knowledge base.** Everything mirrors into an
  [Obsidian](https://obsidian.md)-compatible vault — plain Markdown,
  frontmatter, `[[wikilinks]]` — so graph view, backlinks, and Dataview
  queries work out of the box.
- **Keep a record that outlives the transcripts.** Claude Code eventually
  ages transcripts out; your summaries persist in `~/.threads/`.

Everything is local files. Summaries never leave your machine, and the tool
never edits your own notes — its outputs are separate, regenerable, and
disposable.

## Quick start

```bash
pip install -e .        # or, in the arsenal workspace: uv sync --all-packages

threads scan            # summarize your recent sessions (see cost note below)
threads weave           # group summaries into threads
threads serve           # open the dashboard (local URL is printed)
```

`scan` uses one small-model call (Claude Haiku) per substantial session —
typically well under a cent each; a 30-day backlog of ~300 sessions costs a
few dollars, and it's capped per run (`--max-calls`, default 100; `--all`
lifts the cap for a backfill). It is incremental and idempotent: re-running
it summarizes only new or grown sessions and costs nothing when there's
nothing new. Trivial sessions (under 10 messages or 5 minutes) are recorded
without a model call. Needs `ANTHROPIC_API_KEY` (or a logged-in `claude`
CLI) for the summarization calls only — `weave`, `serve`, `note`, and
`pickup` make no model calls, except one optional clustering call per
`weave` to draft candidate threads.

## How your work gets organized

- **Session → summary.** Each session becomes one record: a title, a 3–6
  sentence summary of what was attempted and how it ended, artifacts touched
  (branches, PRs, files), and status signals (wrapped-up / blocked /
  abandoned-midstream / ongoing).
- **Summary → thread.** Sessions are matched to threads using deterministic
  signals first — repos touched, git branches, worker-pool metadata — with a
  model-suggested fallback. Unmatched sessions go to the unfiled inbox;
  clusters of them become auto-drafted candidate threads.
- **Your project notes name the threads (optional but recommended).** Point
  `THREADS_MEMORY_DIR` at a folder of Markdown notes, one per project — each
  filename becomes a thread name, and matching keys against note contents.
  An optional `MEMORY.md` index supplies one status line per project;
  threads whose line reads closed/retired/complete are never flagged
  dormant. Without such a folder, threads bootstrap entirely from candidate
  drafts.
- **Threads → programs → goals.** A simple tree in
  `~/.threads/hierarchy.md` (editable Markdown) groups threads under
  mid-level programs; if you keep a goals folder (`THREADS_GOALS_DIR`),
  goal files that mention thread names become top-level roots
  automatically. Every level rolls up its subtree's activity.

## The dashboard

`threads serve` hosts it locally (or through the
[lobby](../lobby) hub when available), re-rendering every 60 s. You get: the
sortable/filterable thread table (`?sort=relevance|last-activity|sessions|name`,
active-in-N-days / dormant-only / text search — selections stick across
refresh); a collapsible hierarchy tree with roll-ups; a coverage panel
(goals with no active thread, threads under no goal); per-thread drill-down
with artifact links; the unfiled inbox; and candidate threads.
`threads render` prints the same as a Markdown digest; `threads status` is
a one-liner for scripts and shell prompts.

## Parking and resuming work

```bash
# stepping away mid-stream — dump your working context onto the thread:
threads note my-project - --status parked <<'EOF'
## Where this stands
Benchmark runs green; PR not opened yet.
## Next steps
Open the PR; re-run with seed sweep.
EOF

# later, at the top of a fresh session:
threads pickup my-project
```

Notes capture cwd, git branch, and session id automatically, count as
thread activity (a freshly parked thread isn't "dormant"), and can target a
brand-new name to start a thread that has no note file yet. `pickup` prints
the project's status line, all parked notes (newest first), and recent
observed session summaries — a ready-made context pack for you or an agent.

## Open in Obsidian

The vault at `~/.threads/vault/` (refreshed by every `weave`, or on demand
with `threads vault`) mirrors threads, sessions, programs, goals, and
candidates as linked Markdown notes, starting at `INDEX.md`. Open the
folder as a vault (or add it to an existing one): graph view and backlinks
just work, and [Dataview](https://blacksmithgu.github.io/obsidian-dataview/)
can query the frontmatter:

````markdown
```dataview
TABLE relevance, sessions_30d, last_active, dormant
FROM "threads"
SORT relevance DESC
```
````

The vault is a regenerable mirror — safe to delete, never the source of
truth, and pruned of notes whose underlying records vanish.

## Tuning

`~/.threads/config.toml` (written with commented defaults on first run)
holds the knobs: the relevance formula's weights
(`relevance = w_sessions·log1p(sessions_in_window) + w_recency·exp(-days_since_last/tau)`;
defaults `1.0` / `2.0` / `tau=7`, 30-day window), the dormancy threshold
(14 days), scan lookback, and clustering minimums. Adjust freely; nothing
else needs to change.

## Automation

`threads` installs no crontab. A daily refresh is one line
(`crontab -e`):

```cron
0 7 * * * cd $HOME && threads scan && threads weave >> $HOME/.threads/cron.log 2>&1
```

Keep the dashboard alive across logout with
`tmux new-session -d -s threads-dashboard "threads serve"`.

For scripting and CI-style gating, `threads scan --check` and
`threads weave --check` are cheap, offline (zero model calls) health
checks: they exit non-zero unless summaries are complete for the lookback
window and match quality is above threshold, and print what they verified.

## Environment overrides

| variable | points at | default |
|---|---|---|
| `THREADS_HOME` | base dir (`.threads` lives under it) | `~` |
| `THREADS_PROJECTS_DIR` | Claude Code transcript tree | `~/.claude/projects` |
| `THREADS_MEMORY_DIR` | your project-notes folder | `~/jarvis-memory` |
| `THREADS_GOALS_DIR` | your goals folder (optional) | `~/jarvis/goals` |
| `THREADS_CONCIERGE_HOME` | concierge worker pool (optional) | `~/concierge-home` |

Every integration degrades gracefully: with no notes folder, no goals
folder, no worker pool, and no lobby hub, you still get scan → weave →
a local dashboard.

## Privacy

Transcripts can contain sensitive material. Summaries and notes stay under
`~/.threads/` on your machine; nothing is uploaded anywhere except the
transcript excerpts sent to the Claude API for summarization, and the tool
never writes into your notes or transcripts.
