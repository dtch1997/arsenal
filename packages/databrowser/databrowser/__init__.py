"""databrowser — minimal JSONL data browser.

    import databrowser

    # build a static browser into ./out and serve it over Cloudflare
    viewer = databrowser.serve(
        "results.jsonl",
        filter_fields=["model", "split", "score"],   # categorical/continuous inferred
    )
    print(viewer.url)
    ...
    viewer.stop()

Or just build the static site without serving:

    databrowser.build("results.jsonl", "out/", filter_fields=["model"])
"""

from .core import (
    CATEGORICAL,
    CONTINUOUS,
    FilterField,
    SchemaError,
    build,
    load_records,
    validate_schema,
)
from .server import Viewer, serve

__all__ = [
    "build",
    "serve",
    "Viewer",
    "FilterField",
    "SchemaError",
    "validate_schema",
    "load_records",
    "CATEGORICAL",
    "CONTINUOUS",
]

__version__ = "0.1.0"
