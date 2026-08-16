# flare

A **universal, push-based, always-allowed** distress channel. Any agent process
— a Claude Code session, a headless `claude -p` concierge worker, a stagehand
step, a bare training script on a RunPod pod, a cron job — can page Daniel over
Slack with one line, for any reason.

> You have `flare`. Use it any time, for any reason — operational problems,
> being blocked, wasted-money loops, or anything you think Daniel should know,
> including your own state. Low bar; false positives are fine.

Design context: arsenal issue #36.

## Why a CLI (and stdlib only)

- **Zero per-context wiring.** A CLI works identically across interactive
  sessions, headless workers, pods, and pipeline steps — no MCP server, no
  per-session tool registration.
- **`uvx`-able on a bare pod.** flare imports nothing outside the standard
  library (`urllib.request`, `tomllib`, `json`), so `uvx flare "..."` runs with
  no venv and no other deps.
- **Never crashes its caller.** Transport failures are swallowed: the flare is
  still spooled locally, a warning is printed, and the process exits 0.

## Install

```bash
pip install -e .          # from this directory
# or, on a bare pod:
uvx flare "hello from the pod"
```

## Use

```bash
flare "stuck in a toolcall loop on task X" --sev warn --source concierge
flare "backend is 500ing" --sev page --source pod-42
```

```python
import flare
rec = flare.send("dataset download stalled", sev="warn", source="prep")
```

`--sev` / `sev` is one of `info | warn | page`. `--source` / `source` is a short
label for the emitter.

## Transport & config

The Slack **incoming-webhook URL** is resolved from, in order:

1. env `FLARE_WEBHOOK`
2. `~/.config/flare/config.toml`:

   ```toml
   [slack]
   webhook_url = "https://hooks.slack.com/services/…"
   ```

If neither is set, flare still spools the record locally, prints
`no webhook configured; spooled only`, and exits 0.

The Slack message is a `[{sev}] {msg}` headline plus a context line
(`host · cwd · branch · session · task`).

## Spool (always)

Every flare appends a JSONL record to `~/.flare/log.jsonl` — the Slack channel is
never the only history:

```json
{"ts": "...", "sev": "warn", "msg": "...", "source": "concierge",
 "host": "...", "cwd": "...", "git_branch": "...", "session_id": "...",
 "task_id": "...", "sent": true, "suppressed": false}
```

- `git_branch` from `git rev-parse --abbrev-ref HEAD` if `cwd` is a repo, else null.
- `session_id` from `CLAUDE_SESSION_ID`; `task_id` from `CONCIERGE_TASK_ID`
  (null if absent).

## Spam guard

If an **identical `(msg, source)`** was already posted to Slack within the last
**10 minutes**, the flare is spooled with `suppressed: true` and the Slack post
is skipped. (The agent most likely to page in a loop is the one stuck in a loop.)

## Companion changes (outside this repo)

For flare to be useful to background agents it must never trigger a permission
prompt: allowlist `Bash(flare *)` in jarvis and concierge-worker
`.claude/settings.json`, and add the sanction paragraph above to `CLAUDE.md` /
`HOUSE_RULES.md`. See issue #36.

## Env overrides (mostly for tests)

- `FLARE_HOME` — base dir instead of `~` (spool + config resolve under it).
- `FLARE_LOG` — exact spool path.
- `FLARE_CONFIG` — exact config-file path.
