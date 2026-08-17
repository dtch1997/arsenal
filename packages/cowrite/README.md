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

The editor is served through the shared [lobby](https://github.com/dtch1997/lobby) hub —
one tunnel + one index page across all your editors, reports, and dashboards —
so it works even when the draft lives on a remote box. 

Because the AI keeps writing the file *between* your saves, the editor stays
**in sync with the disk**: it polls the draft's content revision, refreshes
itself when the file changes under it (if you have no unsaved edits), and a
save that would silently overwrite a newer on-disk version is refused with a
conflict prompt instead — you choose which version wins. Without this, either
side's save could silently revert the other's work, which looks exactly like
"saving doesn't work".

The preview also **updates as you type** (debounced ~400ms after the last
keystroke) — it POSTs the current buffer to `/api/render`, which renders it
through the *same* code path as a save but never writes to disk, so what you
see always matches what a save would land. When the co-writer edits the file
under you, a **✍️ co-writer edited** chip appears in the header (decaying from
"just now" to a relative timestamp) and, on the auto-refresh, the blocks that
changed briefly flash-highlight in the preview — so the file shifting beneath
you is visible instead of silent.

## Anchored comments

Leave Google-Docs-style **review comments** on the draft without leaving the
file. Select text in the preview, hit the floating **💬 Comment** affordance,
and type — cowrite writes it into the Markdown as an inert HTML comment right
after the enclosing block:

```markdown
Some paragraph the AI wrote. <!-- cowrite[daniel]: tighten this -->
```

The marker *is* the store and the protocol: the AI sees comments in context on
its next re-read of the file — no sidecar, no API. Existing `cowrite[...]`
markers don't show as text; cowrite parses them out and renders them as margin
bubbles linked to their anchor block. **Resolve** on a bubble deletes the
marker (the AI resolves the same way, by removing it once addressed). Insertion
and resolution are ordinary saves through the existing atomic-write +
`X-Base-Rev` path, so a concurrent AI write conflicts (409) and is handled like
any other save. The marker author defaults to `daniel`; override it with
`GET /?author=<name>`. Comments are attached at the end of their block (MVP
block-level anchoring), stay greppable/strippable via the `cowrite[` prefix,
and render nowhere the draft travels (cowrite, GitHub, the lab-notes build).

A **Revert to last commit** button restores the draft to its last-committed
(`git HEAD`) version — handy when a round of edits went the wrong way. It asks
for confirmation (warning if you have unsaved changes), writes the committed
text back to disk atomically, and re-renders. It's a no-op error if the draft
isn't tracked in a git repo.

## Install

```bash
pip install git+https://github.com/dtch1997/cowrite     # or, from a clone: pip install -e .
```

For the public-URL mode the [lobby](https://github.com/dtch1997/lobby) hub needs the
`cloudflared` binary on your PATH
([install guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/));
without it the hub serves locally only. For local-only editing (`--no-tunnel`)
neither is needed.

## Usage

```bash
# start an editor (creates the file empty if it doesn't exist yet)
cowrite serve path/to/draft.md [--slug NAME] [--title "Blogpost"] [--port N]

cowrite serve draft.md --no-tunnel   # localhost only, no hub / public URL
cowrite list                         # active editors (LIVE/dead), across sessions
cowrite stop <slug>                  # tear one down
cowrite stop --all                   # tear every editor down
cowrite prune                        # reap only dead editors, keep live ones
```

`serve` prints the public `https://<hub>…/a/<slug>/` URL to hand to the human, plus
the exact teardown command. Each `--slug` is an independent editor with its own
port + URL, so several drafts can be open at once.

## How it works

- **The round-trip** is a small custom HTTP handler: `GET /` serves the editor,
  `POST /save` does an *atomic* write-back (temp file + `os.replace`, so a save
  can never truncate the draft midway) and returns freshly rendered HTML.
  `POST /revert` reads the committed version via `git show HEAD:<path>` and
  writes it back the same atomic way. `POST /api/render` renders a markdown
  body to an HTML fragment for the typing-time preview — it renders only, never
  touches disk, so it stays off the single write path.
- **Figures / assets** referenced relatively (`![](fig.png)`, `![](plots/x.png)`)
  are served from the draft's own directory, so the preview shows them exactly
  as they'll appear. Path traversal outside that directory is blocked.
- **Detached service.** The HTTP server is launched detached and persists after
  the `serve` command returns; teardown is explicit via `stop` (which also
  unregisters from the hub), so nothing is orphaned silently. Per-editor state
  lives under `~/.cowrite/state` (override with `COWRITE_STATE_DIR` or
  `XDG_STATE_HOME`).
- **Rendering** uses [Python-Markdown](https://python-markdown.github.io/)
  (tables, fenced code, TOC, sane lists) with Pygments highlighting; MathJax
  loads from a CDN in the browser.

## Security note

The public link is an unauthenticated but unguessable quick tunnel that allows
**writing to the file you served** while it's live. Stop it when you're done,
and don't serve drafts you wouldn't want a holder of the URL to edit.

## License

MIT
