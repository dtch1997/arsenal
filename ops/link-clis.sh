#!/bin/bash
# PATH links are a build artifact (cf. jarvis ops/install-cron.sh for the
# crontab). Every agent-facing arsenal CLI gets a ~/.local/bin symlink into
# the workspace venv, so `flare`, `threads note`, `claude-statusline`, etc.
# resolve bare from any session — always to the arsenal-current code, never
# to a stale pip-installed copy.
#
#   ops/link-clis.sh           install/refresh the symlinks
#   ops/link-clis.sh --check   diff live-vs-repo, exit 1 on drift
#
# After adding a CLI to a package: add it to CLIS below, merge, re-run.
set -euo pipefail

CLIS=(
  arxivist
  bellhop
  cairn
  claude-statusline
  cowrite
  databrowser
  desk
  ferry
  flare
  foyer
  gazette
  lobby
  reportly
  threads
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$REPO_ROOT" == *"/.claude/worktrees/"* ]]; then
  echo "refusing to run from a worktree copy — links would target a venv that gets" >&2
  echo "removed with the worktree. Merge first and run from the main checkout." >&2
  exit 1
fi
VENV_BIN="$REPO_ROOT/.venv/bin"
DEST="$HOME/.local/bin"

check=false
[[ "${1:-}" == "--check" ]] && check=true

drift=0
for cli in "${CLIS[@]}"; do
  src="$VENV_BIN/$cli"
  dst="$DEST/$cli"
  if [[ ! -x "$src" ]]; then
    echo "MISSING IN VENV: $cli ($src) — run 'uv sync --all-packages' first" >&2
    drift=1
    continue
  fi
  if [[ "$(readlink "$dst" 2>/dev/null)" == "$src" ]]; then
    continue
  fi
  if $check; then
    echo "DRIFT: $dst -> $(readlink "$dst" 2>/dev/null || echo "$([[ -e "$dst" ]] && echo 'non-symlink file' || echo 'absent')")" >&2
    drift=1
    continue
  fi
  # Preserve any pre-existing non-symlink (e.g. an old pip wrapper) once.
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    mv "$dst" "$dst.pre-arsenal"
    echo "moved aside: $dst -> $dst.pre-arsenal"
  fi
  ln -sfn "$src" "$dst"
  echo "linked: $cli"
done

$check && exit $drift
exit 0
