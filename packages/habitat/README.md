# habitat

A single-user habit tracker on a long-lived RunPod CPU pod. Celebration-first:
it tracks what you *did* — recency, monthly counts, a heatmap to admire — not a
guilt list of daily obligations. (The 1–2 true daily non-negotiables stay in
whatever nags you; habitat is for the "good to do once in a while" tier.)

## Shape

- **`app/`** — the deployable: a stdlib-only `server.py` (SQLite + `http.server`)
  plus a single-page mobile-first frontend. Shipped to the pod as a tarball;
  never imported by the client.
- **`bootstrap.py`** — the only code in the pod's docker start command
  (base64-embedded, lobby.wiki style). Accepts the first token-gated
  `POST /api/code`, then supervises the server; the server self-updates by
  swapping the app dir and exiting 42.
- **`client.py` / `cli.py`** — devbox side: `provision` (find-or-create pod,
  push app), `deploy` (push new code, zero-downtime-ish restart), `backup` /
  `restore` (JSON dump to `~/.habitat/backups/`), `seed`, `status`.

## Persistence model

RunPod CPU pods have no durable volume, so the pod's SQLite file is the *live*
copy only. The durable copy is the local mirror: run `habitat backup` on a cron
(nightly is plenty); after a pod rebuild, `habitat provision && habitat restore`
brings everything back.

## Auth

One shared secret (`HABITAT_TOKEN` on the pod, printed by `provision`,
kept in `~/.habitat/config.json`). Browser: enter it once, a year-long
HttpOnly cookie remembers the device. CLI/cron: `Authorization: Bearer`.

## Quick start

```
habitat provision            # create pod, deploy, print URL + secret
habitat seed my-habits.json  # optional: [{"name": ..., "emoji": ..., "legacy_count": ...}]
habitat status
habitat backup               # put this in crontab
```

Local dev: `HABITAT_TOKEN=dev HABITAT_DATA=/tmp/hab python3 src/habitat/app/server.py`.
