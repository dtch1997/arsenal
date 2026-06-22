# cowrite

**Co-write Markdown drafts with an AI, in the browser.**
- `cowrite` serves a **side-by-side editor**: raw Markdown on the left, rendered HTML (figures, code highlighting, MathJax) on the
right.
- Pressing **⌘/Ctrl+S** writes the edited Markdown back to the file on disk and re-renders the preview.

```
  AI drafts draft.md  →  cowrite serve draft.md  →  human edits in browser, hits ⌘S
       ↑                                                        │
       └──────────── AI re-reads draft.md, keeps writing ◄──────┘
```

The editor is served over a [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/),
so it works even when the draft lives on a remote box. 

## Install

```bash
pip install git+https://github.com/dtch1997/cowrite     # or, from a clone: pip install -e .
```

For the public-URL mode you also need the `cloudflared` binary on your PATH
([install guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).
For local-only editing (`--no-tunnel`) it isn't needed.

## Usage

```bash
# start an editor (creates the file empty if it doesn't exist yet)
cowrite serve path/to/draft.md [--slug NAME] [--title "Blogpost"] [--port N]

cowrite serve draft.md --no-tunnel   # localhost only, no Cloudflare tunnel
cowrite list                         # active editors (LIVE/dead), across sessions
cowrite stop <slug>                  # tear one down
cowrite stop --all                   # tear every editor down
cowrite prune                        # reap only dead editors, keep live ones
```

`serve` prints the public `*.trycloudflare.com` URL to hand to the human, plus
the exact teardown command. Each `--slug` is an independent editor with its own
port + URL, so several drafts can be open at once.

## How it works

- **The round-trip** is a small custom HTTP handler: `GET /` serves the editor,
  `POST /save` does an *atomic* write-back (temp file + `os.replace`, so a save
  can never truncate the draft midway) and returns freshly rendered HTML.
- **Figures / assets** referenced relatively (`![](fig.png)`, `![](plots/x.png)`)
  are served from the draft's own directory, so the preview shows them exactly
  as they'll appear. Path traversal outside that directory is blocked.
- **Detached service.** The HTTP server and the `cloudflared` tunnel are launched
  detached and persist after the `serve` command returns; teardown is explicit
  via `stop`, so nothing is orphaned silently. Per-editor state lives under
  `~/.cowrite/state` (override with `COWRITE_STATE_DIR` or `XDG_STATE_HOME`).
- **Rendering** uses [Python-Markdown](https://python-markdown.github.io/)
  (tables, fenced code, TOC, sane lists) with Pygments highlighting; MathJax
  loads from a CDN in the browser.

## Security note

The public link is an unauthenticated but unguessable quick tunnel that allows
**writing to the file you served** while it's live. Stop it when you're done,
and don't serve drafts you wouldn't want a holder of the URL to edit.

## License

MIT
