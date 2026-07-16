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
foyer url                  # reprint the current tokened URL
foyer token                # print the auth token
```

The printed URL carries `?t=<token>`; the first visit exchanges it for a
cookie. **URL + token = shell access to your box** — treat it like a
password. Rotate by deleting `~/.foyer/token` and restarting.

## Design notes

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
- **Plots** are just the newest images (depth ≤ 3, skipping dotdirs and
  `node_modules`) under the thread's current working directory.

xterm.js (+fit addon) is vendored under `static/` — no CDN at runtime.
