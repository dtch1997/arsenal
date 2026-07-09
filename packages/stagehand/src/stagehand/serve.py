"""Serve a runs/ directory (its status.html) behind a public tunnel.

Two backends, tried in order:

1. **lobby** (https://github.com/dtch1997/lobby) — registers the dashboard with
   the shared hub daemon, so every stagehand run (and cowrite report, etc.) lives
   under ONE tunnel URL with a central index page.
2. **marquee** (https://github.com/dtch1997/marquee) — the original standalone
   per-run tunnel (pluggable providers: cloudflared / localhost.run / ngrok).

Both are imported lazily, so importing stagehand never requires either.

    from stagehand import serve
    url, stop = serve("runs", name="sleeper-sweep")   # hub: https://<hub>…/a/sleeper-sweep/status.html
    url, stop = serve("runs", hub=False)              # standalone: https://….trycloudflare.com/status.html
"""
from __future__ import annotations

from pathlib import Path


def _marquee():
    try:
        import marquee
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "stagehand.serve needs the `lobby` or `marquee` library — install one with "
            "`pip install git+https://github.com/dtch1997/lobby` (shared hub) or "
            "`pip install git+https://github.com/dtch1997/marquee` (standalone tunnel).") from e
    return marquee


def serve(directory, *, entry="status.html", name=None, title=None, port=None,
          provider="cloudflare", wait=40.0, hub=None):
    """Serve `directory` over a local HTTP server + a public tunnel.

    Returns `(url, stop)`. By default (`hub=None`) the dashboard registers with the
    shared `lobby` hub when lobby is installed — one tunnel + index page across all
    runs — and falls back to a standalone `marquee` tunnel otherwise. Force a
    backend with `hub=True` / `hub=False`. `name`/`title` label the run on the hub
    index (name defaults to the directory name); `provider`/`wait` apply to the
    marquee path. Raises RuntimeError if neither library is installed.
    """
    if hub is not False:
        try:
            import lobby
        except ModuleNotFoundError:
            if hub is True:
                raise RuntimeError(
                    "serve(hub=True) needs the `lobby` library — install it with "
                    "`pip install git+https://github.com/dtch1997/lobby`.") from None
        else:
            return lobby.serve_dir(
                str(directory), name=name or Path(directory).resolve().name,
                kind="stagehand", title=title, entry=entry, port=port,
            )
    return _marquee().serve(directory, entry=entry, port=port,
                            provider=provider, wait=wait)


def parse_tunnel_url(text: str):
    """Pull a Cloudflare quick-tunnel URL out of log text (or None). Delegates to
    `marquee` (kept for back-compat)."""
    return _marquee().parse_tunnel_url(text)
