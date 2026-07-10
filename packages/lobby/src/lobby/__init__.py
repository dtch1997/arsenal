"""lobby — one tunnel for all your local apps.

    from lobby import serve
    url = serve(port, name="sleeper-sweep", kind="stagehand")
    # -> https://<hub>.trycloudflare.com/a/sleeper-sweep/
"""

from .client import ensure_hub, hub_url, serve, serve_dir, unregister
from .state import LobbyError
from .tunnel import (
    PROVIDERS,
    Tunnel,
    TunnelError,
    parse_tunnel_url,
    register_provider,
    tunnel,
)

__version__ = "0.3.0"

__all__ = [
    "serve",
    "serve_dir",
    "unregister",
    "ensure_hub",
    "hub_url",
    "LobbyError",
    "tunnel",
    "Tunnel",
    "PROVIDERS",
    "register_provider",
    "TunnelError",
    "parse_tunnel_url",
    "__version__",
]
