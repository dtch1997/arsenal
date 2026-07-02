# cairn

**A minimal, git-friendly dependency-aware issue graph for coding agents.**

A cairn is a stack of trail-marker stones left for whoever comes next — which is
exactly what a persistent issue graph is for a long-horizon agent. `cairn` gives
a coding agent a structured, dependency-aware task list with a "what's ready to
work on now" queue and a persistent project memory, stored as plain JSON files
that live in your repo and merge cleanly across branches and parallel agents.

It's a deliberately small, stdlib-only alternative to heavier trackers: **no
database, no daemon, no telemetry, no network calls, zero runtime dependencies.**

## Install

```bash
pip install git+https://github.com/dtch1997/cairn   # once published
# or, from a local clone:
pip install -e .
```

## Quickstart

```bash
cd your-project
cairn init                              # creates .cairn/

cairn create "Design the API" -p 0
cairn create "Implement the API"        # prints: created cn-b3d1: Implement the API
cairn dep cn-b3d1 --blocked-by cn-a1f2  # implement is blocked by design

cairn ready                             # only unblocked work shows up
cairn claim cn-a1f2                     # take it (assignee + in_progress)
cairn close cn-a1f2                     # done -> implement becomes ready
cairn ready

cairn remember "API must stay backward-compatible with v1"
cairn prime                             # workflow context + ready queue + memory
```

Every data command takes `--json` for machine consumption:

```bash
cairn ready --json
cairn show cn-a1f2 --json
```

## How it stores data

Everything lives under `.cairn/` at your repo root:

```
.cairn/config.json        # {"prefix": "cn"}
.cairn/issues/<id>.json   # ONE file per issue
.cairn/memory.jsonl       # append-only notes surfaced by `prime`
```

**One file per issue is the whole trick.** Two agents (or branches) editing
*different* issues touch *different* files, so git merges them with zero
conflicts — no central database to serialize on. IDs are a hash of the title +
timestamp + random bytes (`cn-a1b2`), so concurrent creators never collide
without a shared counter. Writes are atomic (write-temp-then-rename), so an
interrupted command never corrupts a file.

The tradeoff: two agents editing the *same* issue is last-write-wins locally, or
a single-file git conflict across branches — rare, and trivial to resolve by
hand. If you need true cell-level 3-way merge on a shared issue under heavy
multi-writer load, you want a database-backed tracker; `cairn` optimizes for
simple, legible, and dependency-free instead.

Commit `.cairn/` to share the tracker with collaborators, or add it to
`.gitignore` for a purely local, personal task list.

## Commands

| Command | What it does |
| --- | --- |
| `cairn init [--prefix cn]` | Create a `.cairn/` store here. |
| `cairn create "Title" [-d desc] [-p 0..3] [-t type] [-a who] [-l label]` | Create an issue. |
| `cairn ready` | List open issues with no open blockers (start here). |
| `cairn list [-s status] [-t type] [-a assignee]` | List issues, filtered. |
| `cairn show <id>` | Show one issue in detail. |
| `cairn claim <id> [--as who]` | Take a task: set assignee + `in_progress`. |
| `cairn update <id> [--title/-d/-p/-t/-a/-s ...]` | Edit fields. |
| `cairn close <id> [<id> ...]` | Close one or more issues. |
| `cairn reopen <id>` | Reopen a closed issue. |
| `cairn dep <id> --blocked-by <other> [--unblock <o>] [--parent <p>]` | Manage edges. |
| `cairn remember "insight"` | Append a durable project note. |
| `cairn prime [--json]` | Print agent workflow context + ready queue + memory. |

Priorities run `P0` (most urgent) to `P3` (least). Status is `open`,
`in_progress`, or `closed`. Type is free-form; `task`, `bug`, `epic`, `chore`,
`note` are the advertised ones.

## Using it from an agent

Point your agent at cairn by adding a short section to its instructions file
(`AGENTS.md`, `CLAUDE.md`, etc.):

```markdown
This project uses `cairn` for issue tracking.
- Run `cairn prime` for workflow context and project memory.
- Use `cairn ready`, `cairn claim <id>`, and `cairn close <id>`.
- Record dependencies with `cairn dep <id> --blocked-by <other>`.
- Store durable insights with `cairn remember "…"` — do not create TODO.md files.
```

The library is equally usable directly:

```python
from cairn import Store

store = Store.discover()          # find .cairn/ from cwd upward
issue = store.create("Do the thing", priority=1)
for todo in store.ready():
    print(todo.id, todo.title)
```

## What it deliberately does not do

Compared to full agent trackers, cairn drops: an embedded SQL database,
federation / multi-repo sync, GitHub/Linear/Jira integrations, LLM-based
compaction, git-hook installation, and any form of telemetry. If you need those,
reach for a heavier tool. cairn is for people who want the dependency graph and
the `ready` queue and nothing they have to trust.

## Development

```bash
pip install -e '.[dev]'
pytest
```
