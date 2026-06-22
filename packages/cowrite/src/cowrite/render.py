"""Markdown -> HTML rendering and the editor page template.

The same `render_fragment` is used both to build the initial preview and to
answer each `/save` round-trip, so the preview a human sees while editing
matches exactly what lands on disk.
"""

from __future__ import annotations

import html as _html


def render_fragment(md_text: str) -> str:
    """Render Markdown to an HTML body fragment (no <html>/<head> wrapper)."""
    import markdown  # py-markdown + pygments

    return markdown.markdown(
        md_text,
        extensions=["extra", "tables", "fenced_code", "codehilite", "sane_lists", "toc"],
        extension_configs={"codehilite": {"guess_lang": False}},
    )


def _pygments_css() -> str:
    try:
        from pygments.formatters import HtmlFormatter

        return HtmlFormatter().get_style_defs(".codehilite")
    except Exception:
        return ""


# Preview styling (so the preview matches a typical published look) + editor
# chrome (top bar, split panes, textarea).
_PREVIEW_CSS = """
.preview { max-width: 820px; margin: 0 auto; padding: 1.6rem 1.4rem 4rem;
  font: 16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
.preview h1,.preview h2,.preview h3,.preview h4 { line-height: 1.25; margin-top: 1.8rem; }
.preview h1,.preview h2 { border-bottom: 1px solid #d0d7de; padding-bottom: .3rem; }
.preview a { color: #0969da; }
.preview code { background: #f6f8fa; padding: .15em .35em; border-radius: 5px; font-size: 85%; }
.preview pre { background: #f6f8fa; padding: 1rem; border-radius: 8px; overflow: auto; }
.preview pre code { background: none; padding: 0; font-size: 90%; }
.preview table { border-collapse: collapse; margin: 1rem 0; }
.preview table th,.preview table td { border: 1px solid #d0d7de; padding: .4rem .8rem; }
.preview blockquote { margin: 0; padding: 0 1rem; color: #656d76; border-left: .25rem solid #d0d7de; }
.preview img { max-width: 100%; }
.preview hr { border: 0; height: 1px; background: #d0d7de; }
.preview .codehilite { border-radius: 8px; }
"""

_CHROME_CSS = """
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column;
  font: 14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  color: #1f2328; background: #fff; }
header { display: flex; align-items: center; gap: .9rem; flex: 0 0 auto;
  padding: .55rem .9rem; border-bottom: 1px solid #d0d7de; background: #f6f8fa; }
header .title { font-weight: 600; font-size: 15px; }
header .path { color: #656d76; font-size: 12px; font-family: ui-monospace,SFMono-Regular,Menlo,monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 40vw; }
header .spacer { flex: 1 1 auto; }
header .status { font-size: 12.5px; color: #656d76; min-width: 12ch; text-align: right; }
header .status.dirty { color: #9a6700; }
header .status.saved { color: #1a7f37; }
header .status.err { color: #cf222e; }
button.save { font: inherit; font-weight: 600; cursor: pointer; color: #fff;
  background: #1f883d; border: 1px solid rgba(31,35,40,.15); border-radius: 6px; padding: .35rem .8rem; }
button.save:hover { background: #1a7f37; }
button.save:disabled { background: #94d3a2; cursor: default; }
.split { flex: 1 1 auto; display: flex; min-height: 0; }
.pane { flex: 1 1 50%; min-width: 0; overflow: auto; }
.pane.edit { border-right: 1px solid #d0d7de; display: flex; }
textarea { flex: 1 1 auto; width: 100%; border: 0; outline: none; resize: none;
  padding: 1.2rem 1.3rem; tab-size: 2;
  font: 13.5px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color: #1f2328; background: #fff; }
.pane.view { background: #fff; }
.hint { font-size: 11.5px; color: #8c959f; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  header { background: #161b22; border-color: #30363d; }
  header .path, header .status { color: #8b949e; }
  textarea { color: #e6edf3; background: #0d1117; }
  .pane.edit { border-color: #30363d; } .pane.view { background: #0d1117; }
  .preview a { color: #4493f8; } .preview code,.preview pre { background: #161b22 !important; }
  .preview table th,.preview table td { border-color: #30363d; }
  .preview h1,.preview h2 { border-color: #30363d; } .preview hr { background: #30363d; }
}
"""

_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
<script>
  window.MathJax = { tex: { inlineMath: [['$','$'],['\\\\(','\\\\)']],
                            displayMath: [['$$','$$'],['\\\\[','\\\\]']] },
                     options: { skipHtmlTags: ['script','noscript','style','textarea','pre'] } };
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
</head>
<body>
<header>
  <span class="title">__TITLE__</span>
  <span class="path">__PATH__</span>
  <span class="spacer"></span>
  <span class="hint">⌘/Ctrl+S to save &amp; render</span>
  <span class="status" id="status">loaded</span>
  <button class="save" id="save">Save</button>
</header>
<div class="split">
  <div class="pane edit"><textarea id="src" spellcheck="false">__MD__</textarea></div>
  <div class="pane view"><div class="preview" id="preview">__PREVIEW__</div></div>
</div>
<script>
const src = document.getElementById('src');
const preview = document.getElementById('preview');
const status = document.getElementById('status');
const saveBtn = document.getElementById('save');
let clean = src.value;          // last-saved content
let saving = false;

function setStatus(text, cls) { status.textContent = text; status.className = 'status' + (cls ? ' ' + cls : ''); }
function markDirty() { if (src.value !== clean) setStatus('● unsaved', 'dirty'); else setStatus('saved', 'saved'); }

async function save() {
  if (saving || src.value === clean) { return; }
  saving = true; saveBtn.disabled = true; setStatus('saving…');
  try {
    const r = await fetch('/save', { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: src.value });
    const data = await r.json();
    if (!r.ok || !data.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
    preview.innerHTML = data.html;
    if (window.MathJax && MathJax.typesetPromise) { MathJax.typesetClear && MathJax.typesetClear([preview]); MathJax.typesetPromise([preview]); }
    clean = data.saved;
    setStatus('✓ saved ' + data.at, 'saved');
  } catch (e) {
    setStatus('✗ ' + e.message, 'err');
  } finally {
    saving = false; saveBtn.disabled = false;
  }
}

src.addEventListener('input', markDirty);
saveBtn.addEventListener('click', save);
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
});
window.addEventListener('beforeunload', (e) => { if (src.value !== clean) { e.preventDefault(); e.returnValue = ''; } });
// Tab inserts two spaces instead of moving focus.
src.addEventListener('keydown', (e) => {
  if (e.key === 'Tab') { e.preventDefault();
    const s = src.selectionStart, en = src.selectionEnd;
    src.value = src.value.slice(0, s) + '  ' + src.value.slice(en);
    src.selectionStart = src.selectionEnd = s + 2; markDirty(); }
});
if (window.MathJax && MathJax.typesetPromise) { MathJax.typesetPromise([preview]); }
</script>
</body></html>
"""


def build_page(md_text: str, title: str, disk_path: str) -> str:
    """Build the full editor HTML page for a draft."""
    css = _CHROME_CSS + "\n" + _PREVIEW_CSS + "\n" + _pygments_css()
    return (
        _PAGE.replace("__CSS__", css)
        .replace("__TITLE__", _html.escape(title))
        .replace("__PATH__", _html.escape(disk_path))
        .replace("__MD__", _html.escape(md_text))
        .replace("__PREVIEW__", render_fragment(md_text))
    )
