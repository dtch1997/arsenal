#!/usr/bin/env python3
"""Stub `codex` binary for tests — never touches the network or the real CLI.

It (a) records the argv it was invoked with (so tests can assert flag
construction: sandbox mapping, --output-schema, resume ordering, -c MCP
overrides) and (b) replays a recorded `codex exec --json` event stream from a
file, then exits with a configurable code. All wiring is via env vars so the
concierge codex wrapper can spawn it unmodified as its `codex_bin`.

  CODEX_STUB_ARGV   -> file to write the received argv to (one arg per line)
  CODEX_STUB_EVENTS -> file whose contents are streamed verbatim to stdout
  CODEX_STUB_RC     -> exit code (default 0)
"""
import os
import sys

argv_path = os.environ.get("CODEX_STUB_ARGV")
if argv_path:
    with open(argv_path, "w") as f:
        f.write("\n".join(sys.argv[1:]))

events_path = os.environ.get("CODEX_STUB_EVENTS")
if events_path and os.path.exists(events_path):
    with open(events_path) as f:
        sys.stdout.write(f.read())
    sys.stdout.flush()

sys.exit(int(os.environ.get("CODEX_STUB_RC", "0")))
