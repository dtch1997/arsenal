# statusline

Claude Code status line renderer. Claude Code invokes the configured
`statusLine` command on every refresh, piping a JSON payload (model,
context_window, cost, workspace, ...) on stdin; whatever the command prints
becomes the status line. This package is that command:

```
[Opus] Context: 75% [███████████░░░░] | $0.123
```

colored green/yellow/red by remaining context (>60% / >30% / below).

It replaces the original unversioned `~/.claude/statusline.sh` (a bash
script that spawned python3 three times per refresh) with one stdlib-only
Python process, byte-identical output.

## Wiring

`~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline.sh"
  }
}
```

and `~/.claude/statusline.sh` is a thin shim into the arsenal venv:

```bash
#!/bin/bash
# Canonical source: arsenal packages/statusline. Edit there, not here.
exec "$HOME/jarvis/repos/arsenal/.venv/bin/claude-statusline"
```

(`uv sync --all-packages` at the arsenal root installs `claude-statusline`
into that venv.)

## Extending

The line is a list of segment functions in `render.py` — each takes the
stdin payload dict and returns a string (or `None` to be skipped). Add a
function, append it to `SEGMENTS`, done. See the payload's full schema in
the Claude Code docs (statusline JSON input) or by dumping stdin to a file
from a scratch segment.

## Test

```bash
uv run pytest packages/statusline/tests
```
