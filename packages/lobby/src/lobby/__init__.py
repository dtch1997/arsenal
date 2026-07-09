"""lobby — one tunnel for all your local apps.

    from lobby import serve
    url = serve(port, name="sleeper-sweep", kind="stagehand")
    # -> https://<hub>.trycloudflare.com/a/sleeper-sweep/
"""

from .client import ensure_hub, hub_url, serve, serve_dir, unregister
from .state import LobbyError

__version__ = "0.2.0"

__all__ = [
    "serve",
    "serve_dir",
    "unregister",
    "ensure_hub",
    "hub_url",
    "LobbyError",
    "__version__",
]
