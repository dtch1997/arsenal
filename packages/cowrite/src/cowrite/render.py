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
button.revert { font: inherit; font-weight: 600; cursor: pointer; color: #1f2328;
  background: #f6f8fa; border: 1px solid rgba(31,35,40,.15); border-radius: 6px; padding: .35rem .8rem; }
button.revert:hover { background: #eef1f4; }
button.revert:disabled { opacity: .55; cursor: default; }
.split { flex: 1 1 auto; display: flex; min-height: 0; }
.pane { min-width: 0; overflow: auto; }
.pane.edit { flex: 0 0 var(--edit-width, 50%); border-right: 1px solid #d0d7de; display: flex; }
.pane.view { flex: 1 1 auto; }
.gutter { flex: 0 0 6px; cursor: col-resize; background: #d0d7de; align-self: stretch;
  position: relative; user-select: none; touch-action: none; }
.gutter:hover, .gutter.dragging { background: #0969da; }
.gutter::before { content: ""; position: absolute; top: 0; bottom: 0; left: -4px; right: -4px; }
body.resizing { cursor: col-resize; user-select: none; }
body.resizing iframe, body.resizing textarea { pointer-events: none; }
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
  button.revert { color: #e6edf3; background: #21262d; border-color: #30363d; }
  button.revert:hover { background: #30363d; }
  textarea { color: #e6edf3; background: #0d1117; }
  .pane.edit { border-color: #30363d; } .pane.view { background: #0d1117; }
  .gutter { background: #30363d; } .gutter:hover, .gutter.dragging { background: #4493f8; }
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
  <button class="revert" id="revert" title="Discard changes and restore the last committed (git HEAD) version">Revert to last commit</button>
  <button class="save" id="save">Save</button>
</header>
<div class="split">
  <div class="pane edit"><textarea id="src" spellcheck="false">__MD__</textarea></div>
  <div class="gutter" id="gutter" title="Drag to resize · double-click to reset"></div>
  <div class="pane view"><div class="preview" id="preview">__PREVIEW__</div></div>
</div>
<script>
const src = document.getElementById('src');
const preview = document.getElementById('preview');
const status = document.getElementById('status');
const saveBtn = document.getElementById('save');
const revertBtn = document.getElementById('revert');
let clean = src.value;          // last-saved content
let rev = '__REV__';            // rev of the disk state this editor is based on
let saving = false;

function setStatus(text, cls) { status.textContent = text; status.className = 'status' + (cls ? ' ' + cls : ''); }
function markDirty() { if (src.value !== clean) setStatus('● unsaved', 'dirty'); else setStatus('saved', 'saved'); }

function applyRendered(data) {
  preview.innerHTML = data.html;
  if (window.MathJax && MathJax.typesetPromise) { MathJax.typesetClear && MathJax.typesetClear([preview]); MathJax.typesetPromise([preview]); }
}

async function save(baseRev) {
  if (saving || src.value === clean) { return; }
  saving = true; saveBtn.disabled = true; setStatus('saving…');
  try {
    const r = await fetch('/save', { method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8', 'X-Base-Rev': baseRev || rev },
      body: src.value });
    const data = await r.json();
    if (r.status === 409 && data.conflict) {
      // The co-writer (AI or another tab) saved a newer version after we last
      // synced. Never clobber it silently — ask which version wins.
      saving = false; saveBtn.disabled = false;
      if (confirm('The draft changed on disk while you were editing (your co-writer ' +
                  'saved a newer version).\\n\\nOK = overwrite it with YOUR version.\\n' +
                  'Cancel = keep editing (the newer disk version stays).')) {
        return save(data.rev);  // retry, based on the disk state we just saw
      }
      setStatus('⚠ newer version on disk', 'err');
      return;
    }
    if (!r.ok || !data.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
    applyRendered(data);
    clean = data.saved;
    rev = data.rev;
    setStatus('✓ saved ' + data.at, 'saved');
  } catch (e) {
    setStatus('✗ ' + e.message, 'err');
  } finally {
    saving = false; saveBtn.disabled = false;
  }
}

// The co-writer keeps editing the file between our saves: poll the disk rev,
// and when it moves, refresh the editor (no local edits) or flag the
// divergence (local edits in flight — the save-time conflict prompt decides).
let pollBusy = false, pollFails = 0;
async function pollDisk() {
  if (pollBusy || saving) { return; }
  pollBusy = true;
  try {
    const r = await fetch('/api/state');
    const state = await r.json();
    if (pollFails >= 3) { markDirty(); }  // recovered: clear the lost-connection status
    pollFails = 0;
    if (state.rev !== rev) {
      if (src.value === clean) {
        const doc = await (await fetch('/api/doc')).json();
        const scroll = src.scrollTop;
        src.value = doc.md; clean = doc.md; rev = doc.rev;
        src.scrollTop = scroll;
        applyRendered(doc);
        setStatus('↻ updated from disk', 'saved');
      } else {
        // Keep `rev` at our base so the next save 409s and prompts.
        setStatus('⚠ changed on disk — saving will ask', 'err');
      }
    }
  } catch (e) {
    if (++pollFails === 3) { setStatus('✗ lost connection to editor server', 'err'); }
  } finally {
    pollBusy = false;
  }
}
setInterval(pollDisk, 2000);

async function revert() {
  if (saving) { return; }
  const dirty = src.value !== clean;
  if (!confirm('Restore this draft to its last committed (git HEAD) version?' +
               (dirty ? '\\n\\nUnsaved changes in the editor will be discarded.' : ''))) { return; }
  saving = true; saveBtn.disabled = true; revertBtn.disabled = true; setStatus('reverting…');
  try {
    const r = await fetch('/revert', { method: 'POST' });
    const data = await r.json();
    if (!r.ok || !data.ok) { throw new Error(data.error || ('HTTP ' + r.status)); }
    src.value = data.saved;
    applyRendered(data);
    clean = data.saved;
    rev = data.rev;
    setStatus('↩ reverted ' + data.at, 'saved');
  } catch (e) {
    setStatus('✗ ' + e.message, 'err');
  } finally {
    saving = false; saveBtn.disabled = false; revertBtn.disabled = false;
  }
}

src.addEventListener('input', markDirty);
saveBtn.addEventListener('click', () => save());
revertBtn.addEventListener('click', revert);
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

// Draggable split: the gutter sets the edit pane's width as a % of the split.
const split = document.querySelector('.split');
const gutter = document.getElementById('gutter');
const editPane = document.querySelector('.pane.edit');
const WKEY = 'cowrite:edit-width';
function applyWidth(pct) {
  pct = Math.min(85, Math.max(15, pct));
  editPane.style.setProperty('--edit-width', pct + '%');
}
const saved = parseFloat(localStorage.getItem(WKEY));
if (saved) { applyWidth(saved); }
function onMove(e) {
  const r = split.getBoundingClientRect();
  const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
  applyWidth((x / r.width) * 100);
}
function stop() {
  document.body.classList.remove('resizing');
  gutter.classList.remove('dragging');
  window.removeEventListener('mousemove', onMove);
  window.removeEventListener('touchmove', onMove);
  window.removeEventListener('mouseup', stop);
  window.removeEventListener('touchend', stop);
  const cur = editPane.style.getPropertyValue('--edit-width');
  if (cur) { localStorage.setItem(WKEY, parseFloat(cur)); }
}
function start(e) {
  e.preventDefault();
  document.body.classList.add('resizing');
  gutter.classList.add('dragging');
  window.addEventListener('mousemove', onMove);
  window.addEventListener('touchmove', onMove, { passive: false });
  window.addEventListener('mouseup', stop);
  window.addEventListener('touchend', stop);
}
gutter.addEventListener('mousedown', start);
gutter.addEventListener('touchstart', start, { passive: false });
gutter.addEventListener('dblclick', () => { editPane.style.removeProperty('--edit-width'); localStorage.removeItem(WKEY); });
</script>
</body></html>
"""


def build_page(md_text: str, title: str, disk_path: str, rev: str) -> str:
    """Build the full editor HTML page for a draft."""
    css = _CHROME_CSS + "\n" + _PREVIEW_CSS + "\n" + _pygments_css()
    return (
        _PAGE.replace("__CSS__", css)
        .replace("__TITLE__", _html.escape(title))
        .replace("__PATH__", _html.escape(disk_path))
        .replace("__REV__", rev)
        .replace("__MD__", _html.escape(md_text))
        .replace("__PREVIEW__", render_fragment(md_text))
    )
