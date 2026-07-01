"""Serve a runs/ directory (its status.html) behind a public tunnel.

The serving + tunnelling implementation moved to the standalone **marquee** library
(pluggable providers: cloudflared / localhost.run / ngrok). This stays as a thin,
lazy re-export so `from stagehand import serve` keeps working — `marquee` is
imported only when you call `serve()`, so importing stagehand never requires it.

    from stagehand import serve
    url, stop = serve("runs")                       # cloudflared -> https://….trycloudflare.com/status.html
    url, stop = serve("runs", provider="localhost.run")   # other providers via marquee

Install the implementation with `pip install git+https://github.com/dtch1997/marquee`.
"""
from __future__ import annotations


def _marquee():
    try:
        import marquee
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "stagehand.serve moved to the `marquee` library — install it with "
            "`pip install git+https://github.com/dtch1997/marquee`.") from e
    return marquee


def serve(directory, *, entry="status.html", port=None, provider="cloudflare", wait=40.0):
    """Serve `directory` over a local HTTP server + a public tunnel (via `marquee`).

    Returns `(url, stop)`; `provider` selects the tunnel backend (default
    cloudflared). See the marquee docs for the provider list. Raises RuntimeError if
    marquee isn't installed, or `marquee.TunnelError` if the tunnel can't be set up.
    """
    return _marquee().serve(directory, entry=entry, port=port,
                            provider=provider, wait=wait)


def parse_tunnel_url(text: str):
    """Pull a Cloudflare quick-tunnel URL out of log text (or None). Delegates to
    `marquee` (kept for back-compat)."""
    return _marquee().parse_tunnel_url(text)
