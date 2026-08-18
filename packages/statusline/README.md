# statusline

Claude Code status line renderer. Claude Code invokes the configured
`statusLine` command on every refresh, piping a JSON payload (model,
context_window, cost, workspace, ...) on stdin; whatever the command prints
becomes the status line. This package is that command:

```
[Opus] Context: 75% [███████████░░░░] | $0.123
· repro logit-interp headline
⚑ open PR; push branch
```

Row 1 (vitals) is colored green/yellow/red by remaining context
(>60% / >30% / below); the other rows appear only when they have content:

- **topic** (dim) — what the session is about: the harness's auto-generated
  `session_name`, overridable with `claude-statusline note "..."`.
- **wrap-up flags** (bold red `⚑`) — things to do before ending the
  session. From inside a session (keys on `$CLAUDE_CODE_SESSION_ID`):

  ```bash
  claude-statusline flag "open PR" "update memory stub"
  claude-statusline unflag "open PR"    # substring match; or --all
  claude-statusline show
  ```

  State lives in `~/.claude/statusline/sessions/<session_id>.json`
  (pruned after 30 days).

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
into that venv.) The CLI is also symlinked onto PATH so sessions can call
it bare:

```bash
ln -sf ~/jarvis/repos/arsenal/.venv/bin/claude-statusline ~/.local/bin/claude-statusline
```

## Extending

The line is a list of segment functions in `render.py` — each takes the
stdin payload dict and returns a string (or `None` to be skipped),
arranged into rows by the `LINES` list — every row renders as its own
status-line row, empty rows are dropped, and segments style themselves
(row 1 additionally gets the context tint). Add a segment to a row, or a
new row, done. See the payload's full schema in
the Claude Code docs (statusline JSON input) or by dumping stdin to a file
from a scratch segment.

## Test

```bash
uv run pytest packages/statusline/tests
```
