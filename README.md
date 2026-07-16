# arsenal

The utility monorepo: every dtch1997 research-infrastructure tool in one
repo, one lockfile, one venv. Each tool keeps its own package identity
(name, version, CLI, import path) — `packages/<tool>/` is a normal
installable package; the workspace just makes them develop and refactor
together.

| Package | What it is |
|---|---|
| [`lobby`](packages/lobby) | one tunnel for all local apps: hub daemon + index + `/a/<name>/` reverse proxy + pluggable tunnel providers (`lobby.tunnel`) |
| [`ferry`](packages/ferry) | bytes ↔ GCS: `push`/`pull`/`Remote` by path (rclone) + `ferry.cas` content-addressed store (dist name `ferry-sync`) |
| [`databrowser`](packages/databrowser) | JSONL → static HTML browser, served through the lobby hub |
| [`arxivist`](packages/arxivist) | arXiv papers → structured, agent-legible markdown (native-HTML-first parse, PDF fallback, outline/section CLI) |
| [`foyer`](packages/foyer) | web front door for the tmux sessions your agents live in: session sidebar + live terminal (websocket PTY bridge) + plots/notes panes, own tunnel via `lobby.tunnel` |

More tools migrate in as waves: stagehand, cowrite, cairn, reportly,
bellhop, then concierge. Histories were preserved on merge
(`git filter-repo --to-subdirectory-filter`), so `git log packages/<tool>`
goes back to each tool's first commit.

## Use

```bash
uv sync --all-packages          # one venv, every tool editable, every CLI
uv run lobby status
uv run pytest packages/lobby/tests
```

Install a single tool anywhere (plain pip works):

```bash
pip install "git+https://github.com/dtch1997/arsenal#subdirectory=packages/lobby"
```

Cross-package dependencies are declared as those subdirectory URLs, with
`[tool.uv.sources] <name> = { workspace = true }` overriding to the local
editable inside the workspace.

## Conventions

- One tool = one directory under `packages/`, with its own `pyproject.toml`,
  `src/` (or flat) layout, and `tests/`.
- Tools stay import-compatible with their pre-monorepo selves — no
  `arsenal.` namespace.
- Zero-dep cores, lazy heavy imports behind extras (`ferry-sync[gcs]`).
- CI runs each package's tests separately (matrix over `packages/*`).
