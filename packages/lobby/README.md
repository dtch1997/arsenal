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
the file server for you, and a context manager that unregisters on exit:

```python
from lobby import serve_dir, serving

url, stop = serve_dir("runs/", name="my-flow", kind="stagehand", entry="status.html")

with serving(port, name="run-42", kind="test") as url:
    ...  # unregistered (not just "ended") when the block exits
```

CLI:

```
lobby status [--json]     # hub URL + per-app public URLs (live/ended)
lobby serve <port|dir>    # register a listening port, or serve a directory
                          #   [--name --kind --title --entry --no-tunnel]
lobby url [name]          # print the hub's (or one app's) public URL
lobby open [name]         # ...or open it in the browser
lobby logs [-f] [-n N]    # hub daemon log
lobby up [--no-tunnel]
lobby stop <name> | --all | --hub
lobby prune               # forget apps that are no longer running
```

The index page is a live dashboard: apps are grouped by the directory they
were launched from, kinds are color-coded, and an inline script polls the hub
so cards appear/expire in place (no full-page refresh). Everything is
server-rendered stdlib HTML — no frameworks, no external assets — and the
page is read-only by design, since the tunnel makes it public.

## Wiki (persistent, cloud-hosted)

The hub is ephemeral by design — its URL changes on every restart and apps die
with their processes. For write-ups that should stay up, `lobby.wiki` is the
opposite: a tiny wiki server on a cheap always-on RunPod CPU pod (~$0.03/hr)
with a **stable public URL**. The content model is a plain directory tree that
you pull, edit arbitrarily, and push back — no git, no build step, no GitHub
Pages.

```python
from lobby import wiki

w = await wiki.server()                  # find-or-create (needs RUNPOD_API_KEY)
tree = await w.pull()                    # whole tree -> ~/.lobby/wiki/wiki/
(tree / "report.md").write_text("# hi")
await w.push()                           # tree -> server, atomic total replace
await w.add("results-site/")             # sugar: pull + copy in + push
await w.rm("report.md"); await w.ls(); await w.status()
await w.stop()                           # halt the pod; destroy() terminates it
```

- `server(name)` is find-or-create: the locally recorded pod, else any RunPod
  pod named `lobby-wiki-<name>` (a second machine adopts an existing wiki —
  the write token is recovered from the pod env), else a fresh pod. Different
  names are fully independent wikis.
- The server renders the tree browsably: `.md` files become pages (`?raw` for
  the source), directories serve their `index.html`/`index.md` or a generated
  listing, everything else is served as bytes. Dotfiles are hidden.
- Reads are public (it's for public-facing docs); pushes need the bearer
  token (kept in `~/.lobby/wiki/<name>.json`).
- The local mirror (`~/.lobby/wiki/<name>/`) is the durable copy — RunPod CPU
  pods have no persistent volume, so after a pod restart or recreate, `push()`
  restores the tree. The proxy URL is stable for the pod's lifetime.

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
- The tunnel is built in (`lobby.tunnel`, absorbed from the retired
  [marquee](https://github.com/dtch1997/marquee) library): pluggable providers
  behind one seam — cloudflare quick tunnels by default, `localhost.run` and
  `ngrok` included, custom ones via `register_provider`. Pick with
  `lobby up --provider …` or `LOBBY_PROVIDER`. No `cloudflared` binary?
  The hub still runs, local-only.

The hub URL is stable for the daemon's lifetime — one long-lived quick-tunnel
URL instead of one per app. (A permanently-stable named tunnel would be a new
`lobby.tunnel` provider; the seam is there.)

## Install

```
pip install "lobby @ git+https://github.com/dtch1997/lobby"
```

## Websockets

Not supported (none of the downstream tools use them — they poll or
meta-refresh). If you register a websocket app, its HTTP pages will proxy
fine but upgrades will fail.
