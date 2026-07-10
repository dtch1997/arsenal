"""Serve a runs/ directory (its status.html) behind a public URL.

The serving + tunnelling implementation lives in the **lobby** library
(https://github.com/dtch1997/lobby): every dashboard registers with the shared
hub daemon, so all runs (and cowrite reports, databrowsers, ...) live under ONE
tunnel URL with a central index page. This stays as a thin, lazy wrapper so
`from stagehand import serve` keeps working — `lobby` is imported only when you
call `serve()`, so importing stagehand never requires it.

    from stagehand import serve
    url, stop = serve("runs", name="sleeper-sweep")  # -> https://<hub>…/a/sleeper-sweep/status.html

Install the implementation with `pip install git+https://github.com/dtch1997/lobby`.
"""
from __future__ import annotations

from pathlib import Path


def _lobby():
    try:
        import lobby
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "stagehand.serve is implemented by the `lobby` library — install it with "
            "`pip install git+https://github.com/dtch1997/lobby`.") from e
    return lobby


def serve(directory, *, entry="status.html", name=None, title=None, port=None):
    """Serve `directory` through the shared lobby hub; return `(url, stop)`.

    One tunnel + one index page across all runs — `name`/`title` label this run
    on the hub index (name defaults to the directory name). `stop()` kills the
    file server; the hub then shows the run as ended. Raises RuntimeError if
    lobby isn't installed.
    """
    return _lobby().serve_dir(
        str(directory), name=name or Path(directory).resolve().name,
        kind="stagehand", title=title, entry=entry, port=port,
    )


def parse_tunnel_url(text: str):
    """Pull a Cloudflare quick-tunnel URL out of log text (or None). Delegates to
    `lobby` (kept for back-compat)."""
    return _lobby().parse_tunnel_url(text)
