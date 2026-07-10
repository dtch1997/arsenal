"""Logging for stagehand — stdlib `logging`, silent by default.

The library logs to a single module logger, `logging.getLogger("stagehand")`, with
a `NullHandler` attached so it emits nothing until the *application* configures
logging (the standard library-logging contract). Levels:

  INFO     flow start / finish, early-exit via stop_when
  WARNING  a task raised (captured, not re-raised — so otherwise only visible on
           the dashboard); this is the line that tells you something died
  DEBUG    per-task start / done(+duration) / skipped, and filter prunes

For quick interactive use, `enable_logging("INFO")` attaches a simple stderr
handler. Real apps should configure `logging` themselves instead.
"""
from __future__ import annotations
import logging
import sys

log = logging.getLogger("stagehand")
log.addHandler(logging.NullHandler())          # silent until the app opts in


def enable_logging(level="INFO", *, stream=None):
    """Attach a simple stderr handler to stagehand's logger and set its level — a
    convenience for quick use. Idempotent (won't stack handlers on repeat calls).
    """
    log.setLevel(level if isinstance(level, int)
                 else getattr(logging, str(level).upper()))
    for h in list(log.handlers):               # drop a handler we added before
        if getattr(h, "_stagehand", False):
            log.removeHandler(h)
    h = logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    h._stagehand = True
    log.addHandler(h)
    return log
