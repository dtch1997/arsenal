"""cowrite — co-write Markdown drafts with an AI in the browser.

Point it at a Markdown file and it serves a side-by-side editor (raw Markdown |
rendered HTML preview). Pressing Cmd/Ctrl+S writes the edited Markdown back to
the file on disk and re-renders the preview, so a model can draft → a human
edits in the browser → the model re-reads the same file and keeps writing, over
many rounds.
"""

from .render import build_page, render_fragment

__version__ = "0.1.0"
__all__ = ["build_page", "render_fragment", "__version__"]
