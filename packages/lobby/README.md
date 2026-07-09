# lobby

One tunnel for all your local apps.

Tools like [cowrite](https://github.com/dtch1997/cowrite),
[stagehand](https://github.com/dtch1997/stagehand), and
[databrowser](https://github.com/dtch1997/databrowser) each spin up a local
server plus their own ephemeral `trycloudflare.com` tunnel. Once you have a
few running, you're juggling a pile of random URLs. `lobby` replaces the
per-app tunnels with a single hub: a small daemon that owns **one** tunnel,
shows an **index page** of everything registered, and **reverse-proxies**
each app under a stable path.

```
https://<hub>.trycloudflare.com/                  ← index of all apps
https://<hub>.trycloudflare.com/a/sleeper-sweep/  ← a stagehand dashboard
https://<hub>.trycloudflare.com/a/report-v2/      ← a cowrite report
```

## Usage

The whole downstream API is one drop-in call. Your app is already listening
on `127.0.0.1:<port>`; register it:

```python
from lobby import serve

url = serve(port, name="sleeper-sweep", kind="stagehand", title="Sleeper scaling sweep")
# -> https://<hub>.trycloudflare.com/a/sleeper-sweep/
```

The first `serve()` call anywhere auto-starts the hub daemon (detached, so it
outlives the caller) and brings up its tunnel; every later call from any
process reuses it. There's also a static-directory convenience that spawns
the file server for you:

```python
from lobby import serve_dir

url, stop = serve_dir("runs/", name="my-flow", kind="stagehand", entry="status.html")
```

CLI:

```
lobby status          # hub URL + table of registered apps (live/ended)
lobby up [--no-tunnel]
lobby stop <name> | --all | --hub
lobby prune           # forget apps that are no longer running
```

## How it works

- The hub is a stdlib `ThreadingHTTPServer` on a fixed local port
  (default `4777`, override with `LOBBY_PORT`). State is file-per-app JSON
  under `~/.lobby/` (override with `LOBBY_STATE_DIR`).
- `/a/<name>/*` is reverse-proxied to the app's local port with the prefix
  stripped, so root-mounted apps work unchanged — as long as their pages use
  **relative** URLs for same-origin requests. Root-absolute `Location`
  redirects from backends are rewritten back into the mount.
- Liveness = pid check (when registered) + TCP probe. Dead apps stay on the
  index greyed out as "ended" until you `lobby prune`.
- The tunnel comes from [marquee](https://github.com/dtch1997/marquee)
  (`pip install "lobby[tunnel]"`), default provider cloudflare quick tunnels.
  No marquee / no `cloudflared`? The hub still runs, local-only.

The hub URL is stable for the daemon's lifetime — one long-lived quick-tunnel
URL instead of one per app. (A permanently-stable named tunnel would be a new
marquee provider; the seam is there.)

## Install

```
pip install "lobby[tunnel] @ git+https://github.com/dtch1997/lobby"
```

## Websockets

Not supported (none of the downstream tools use them — they poll or
meta-refresh). If you register a websocket app, its HTTP pages will proxy
fine but upgrades will fail.
