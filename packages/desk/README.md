# desk

The **waiting-on-Daniel inbox**. Jarvis has plenty of channels that *pull* work
toward Daniel; desk is the one page that answers "what is blocked on **me**
right now?" and — via [flare](../flare) — *pushes* newly-appearing items so he
doesn't have to keep looking.

It aggregates four sources into `~/.desk/inbox.md`:

| collector   | surfaces                                                             |
|-------------|----------------------------------------------------------------------|
| `concierge` | pool tasks under `<concierge_home>/tasks/t-*.json` with `status` `blocked`/`failed` (`*.wait.json` sidecars skipped) |
| `prs`       | open PRs per configured repo (`gh pr list`), flagged when older than `pr_age_warn_days` |
| `markers`   | lines in `*.md` under the marker paths containing `BLOCKED-ON-DANIEL` or `standing until Daniel edits` |
| `flares`    | `warn`/`page` entries in `~/.flare/log.jsonl` newer than `flare_stale_days` |

Every collector **degrades gracefully**: a missing directory, a missing `gh`, or
a malformed file yields no items plus a warning in the inbox footer — never a
crash.

## Install

```bash
pip install -e .          # from this directory
```

## Commands

```bash
desk render     # write ~/.desk/inbox.md (sections by kind, oldest/most-severe
                # first, footer with per-collector warnings + a timestamp); prints the path
desk digest     # short plaintext summary: counts per kind + the oldest three items
desk sync       # render, diff item ids vs ~/.desk/state.json, and flare each NEW
                # item (sev=warn, source=desk); update state (removed items just drop out)
desk serve      # serve the inbox through the lobby hub, re-rendering every 60s
                # (falls back to a plain localhost server if lobby is unavailable)
```

## Config

`~/.config/desk/config.toml` is created on first run with the defaults written as
comments. Keys (defaults shown):

```toml
github_repos   = ["ArcadiaImpact/jarvis", "dtch1997/arsenal"]
marker_paths   = ["~/jarvis-memory", "~/jarvis/goals"]
concierge_home = "~/concierge-home"
pr_age_warn_days = 7
flare_stale_days = 7
```

## Recommended cron (not installed for you)

desk deliberately installs **no** crontab. Add these lines yourself
(`crontab -e`) once you're happy with the config:

```cron
# hourly: refresh the inbox and page any newly-appearing blocker
0 * * * * cd $HOME && desk sync >> $HOME/.desk/sync.log 2>&1

# daily 08:30: a plaintext digest, delivered over flare as an info ping
30 8 * * * desk digest | flare "$(cat)" --sev info --source desk-digest
```

(The digest line pipes the summary into `flare` as the message; adjust to taste
— e.g. drop the pipe and just read `~/.desk/inbox.md` from your phone via
`desk serve`.)

## Env overrides (mostly for tests)

- `DESK_HOME` — base dir instead of `~` (`~/.desk` and config resolve under it).
- `DESK_CONFIG` — exact config-file path.
