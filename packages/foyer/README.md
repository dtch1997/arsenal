# foyer

Walk in and see every thread. `foyer` is a web front door for the tmux
sessions your agents live in: a sidebar listing every session (who's active,
where it is, what it last printed), a real terminal attached to the one you
click, and side panes for the plots and notes that go with it.

It replaces the `ssh box` → `tmux attach` → alt-tab loop with one browser tab
that works from any device.

```
+----------+--------------------------------+----------------+
| threads  |                                |  Plots | Notes |
|          |                                |                |
| jarvis-1 |    terminal (xterm.js over     |  newest images |
| jarvis-2 |    a websocket PTY bridge      |  near the      |
| aligne-1 |    onto `tmux attach`)         |  thread's cwd  |
| ...      |                                |                |
+----------+--------------------------------+----------------+
```

## Usage

```
foyer serve                # serve + own cloudflare quick tunnel, prints tokened URL
foyer serve --no-tunnel    # localhost only (ssh -L it yourself)
foyer url                  # reprint the current tokened URL (stable one if relayed)
foyer token                # print the auth token
foyer relay up             # one-time: provision the stable-URL relay pod
foyer relay redeploy       # push new relay code to the SAME pod (URL unchanged)
foyer relay status|delete
```

## Stable URL (`foyer relay`)

Quick tunnels mint a new random URL every restart. `foyer relay up`
provisions a ~$0.03/hr always-on RunPod CPU pod (pattern cribbed from
`lobby.wiki`: REST-created, server code embedded base64 in the docker start
command) whose `https://<pod-id>-8080.proxy.runpod.net` address never
changes. On every `foyer serve`, the devbox publishes its fresh quick-tunnel
URL to the relay's bearer-token control endpoint, and the pod forwards all
traffic there.

The pair is self-healing: the pod persists its target on disk (container
restarts don't forget it) and its accept loop survives anything, while
`foyer serve` runs a keeper that pings every minute and re-publishes whenever
the relay's target fingerprint stops matching the live tunnel.

The pod-side forwarder (`relay_httpd.py`) is a deliberate *byte pump*, not an
HTTP proxy: it rewrites the request head (`Host`, `Connection`) and then
copies bytes both ways — a websocket after its handshake is just TCP, so the
terminal flows through without the relay knowing what a websocket is. Since
the browser only ever sees the stable domain, the auth cookie survives foyer
restarts: enter the token once per device, ever.

The printed URL carries `?t=<token>`; the first visit exchanges it for a
cookie. **URL + token = shell access to your box** — treat it like a
password. Rotate by deleting `~/.foyer/token` and restarting.

## Design notes

- **Thread switching is instant** (after the first visit): the frontend keeps
  one live terminal + websocket per recently-used thread (LRU, cap 8) and
  switching just shows/hides them; the first ~8 threads are pre-warmed on
  page load. Connection setup through the relay chain costs ~0.7s, so paying
  it once per thread instead of per switch is the whole trick. Hidden
  terminals stay laid out (`visibility: hidden`) so every attached foyer
  client reports the same real size — a zero-size client would make tmux
  letterbox the session for everyone.
- **tmux stays the substrate.** foyer never creates or kills your sessions;
  each browser connection is just one more `tmux attach` client (SIGTERM'd →
  detached when the socket closes). Caveat: tmux sizes a window to its
  smallest attached client, so a session simultaneously attached from a tiny
  ssh terminal will letterbox the browser view.
- **Own tunnel, not the lobby hub.** The hub's stdlib reverse proxy buffers
  whole responses and can't pass websockets, and its index is public by
  design while foyer is a shell. foyer reuses the shared `lobby.tunnel`
  provider seam for its own quick tunnel instead. (Lobby WS passthrough is a
  possible follow-up; then foyer could sit behind the hub like everything
  else.)
- **Sidebar status** comes from the active pane: `pane_current_command`
  (green dot when it's an agent process), `pane_title` (Claude Code writes
  its live status there), and a `capture-pane` tail as the preview line.
- **Notes** are plain markdown files in `~/.foyer/notes/<session>.md` —
  greppable, syncable, nothing proprietary.
- **Plots** are the newest images (depth ≤ 3, skipping dotdirs and
  `node_modules`) under the thread's *plot root* — by default its current
  working directory, overridable per thread from the Plots pane (persisted in
  `~/.foyer/plotroots.json`); useful when several threads share a cwd.
- **Thread order** is yours: drag cards in the sidebar to rearrange; the
  order persists (`~/.foyer/order.json`) and unlisted/new sessions append
  below, sorted by recency.
- **Threads are born and renamed in the UI.** "＋ new thread" creates a
  detached tmux session in the configured workspace and types the configured
  command into it (defaults: `$HOME` and `claude`; set
  `~/.foyer/config.json` → `{"workspace": "~/jarvis", "command": "claude"}`).
  The command goes in via send-keys so the shell — and the thread — survive
  the program exiting. Double-click a thread's name to rename it; notes,
  plot roots, and sidebar order follow the new name. To kill a thread, hover
  its card and click ✕ twice (first click arms a red "sure?" for 3s) — the
  tmux session dies, its plot root and order slot are cleaned up, and its
  notes file is deliberately kept.

URLs in terminal output (plain text or OSC 8 hyperlinks) are **cmd/ctrl+click**
to open, matching terminal convention.

xterm.js (+fit and web-links addons) is vendored under `static/` — no CDN at
runtime.
