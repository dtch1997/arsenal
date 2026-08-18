"""gazette — consumer-mode PR flow.

Agents label every PR with a lane at open time; a nightly ``gazette sweep``
merges what the lane allows, and a morning ``gazette notes`` renders patch
notes (merged yesterday / in the veto window / waiting on Daniel) so Daniel
reads what happened instead of reviewing every PR.

Lanes (GitHub labels):

- ``lane:auto``    — docs, drafts, wiki, dashboards: merged on green.
- ``lane:delay``   — conventions, crons, tool behavior: merged after a veto
  window (default 36h) unless vetoed. Unlabeled PRs default here.
- ``lane:blocked`` — money, credentials, external-facing: never cron-merged.

Veto = add the ``veto`` label or request changes on the PR. Auto-lane PRs that
touch protected paths (CLAUDE.md, ops/, …) are demoted to delay; anything
touching credential-like paths is demoted to blocked. Demotions and
unclassified PRs are surfaced in the patch notes as anomalies.
"""

from __future__ import annotations

from .lanes import Lane, resolve_lane, decide

__all__ = ["Lane", "resolve_lane", "decide"]

__version__ = "0.1.0"
