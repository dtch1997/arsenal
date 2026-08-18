# gazette

Consumer-mode PR flow: Daniel reads morning **patch notes** about what merged
instead of reviewing every PR. Agents declare a lane on each PR at open time;
a nightly sweep merges what the lane allows; a veto is one label away.

## Lanes (GitHub labels)

| label          | what belongs here                                   | cron behavior                        |
| -------------- | --------------------------------------------------- | ------------------------------------ |
| `lane:auto`    | docs, drafts, wiki, dashboards, memory-adjacent     | merged nightly on green checks       |
| `lane:delay`   | conventions (CLAUDE.md), ops/cron.tab, tool behavior | merged after 36h unless vetoed       |
| `lane:blocked` | money, credentials, external-facing, destructive     | never cron-merged — desk/flare flow  |

- **Unlabeled** PRs default to `lane:delay` and are flagged as unclassified.
- **Demotions are the backstop**: an auto-labeled PR touching protected paths
  (`CLAUDE.md`, `ops/**`, `.claude/**`, …) drops to delay; anything touching
  credential-like paths (`.env*`, `*secret*`, `*credential*`) drops to blocked
  — regardless of label. Demotions appear in the patch notes as anomalies.
- **Veto** = add the `veto` label or request changes on the PR. Drafts are
  never touched.

## CLI

```
gazette sweep [--dry-run]   # nightly merge pass (squash; deletes remote branch,
                            # leaves local worktrees to sessions)
gazette notes [--flare]     # morning patch notes → stdout + ~/.gazette/notes/YYYY-MM-DD.md
gazette status              # one-line digest
```

Patch notes sections: *Merged (last 24h)* · *In the pipeline* (with time left
in the veto window and the changed-file list) · *Waiting on you* · *Anomalies*
(unclassified PRs, demotions, failing checks, merge errors).

Config: `~/.config/gazette/config.toml` (created with commented defaults on
first run) — repos, `delay_hours`, protected/blocked globs. Spool:
`~/.gazette/log.jsonl` (every sweep decision) and `~/.gazette/notes/`.

Uses the `gh` CLI for all GitHub access (existing auth); sends the morning
digest through `flare`. Policy lives in pure functions (`lanes.decide`) so the
whole merge policy is offline-testable.
