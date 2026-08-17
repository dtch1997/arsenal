"""Markdown -> HTML rendering and the editor page template.

The same `render_fragment` is used both to build the initial preview and to
answer each `/save` round-trip, so the preview a human sees while editing
matches exactly what lands on disk.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

# Anchored-comment marker: `<!-- cowrite[<author>]: <text> -->`. The draft file
# itself is the store — the AI sees comments in context on its next re-read and
# resolves one by deleting its marker. Markers are inert HTML comments so they
# never render as text (in cowrite, GitHub, or the lab-notes build); cowrite
# parses them out and shows them as margin bubbles instead.
_COMMENT_RE = re.compile(r"<!--\s*cowrite\[([^\]]*)\]:\s*(.*?)\s*-->", re.S)
_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_FENCE_CLOSE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
_LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s")
_LINKDEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*\S")


def strip_comment_markers(md_text: str) -> str:
    """Remove cowrite comment markers (they're shown as bubbles, not text)."""
    return _COMMENT_RE.sub("", md_text)


def render_fragment(md_text: str) -> str:
    """Render Markdown to an HTML body fragment (no <html>/<head> wrapper).

    Comment markers are stripped first so the preview stays pristine and its
    top-level blocks line up 1:1 with the source blocks the bubbles anchor to.
    """
    import markdown  # py-markdown + pygments

    return markdown.markdown(
        strip_comment_markers(md_text),
        extensions=["extra", "tables", "fenced_code", "codehilite", "sane_lists", "toc"],
        extension_configs={"codehilite": {"guess_lang": False}},
    )


def _raw_blocks(md_text: str) -> list[tuple[int, int]]:
    """Split into blank-line-separated blocks as (start, end) char spans, with
    fenced code treated as opaque (blank lines inside a fence don't split)."""
    blocks: list[tuple[int, int]] = []
    pos = 0
    start = None
    end = 0
    in_fence = False
    for line in md_text.splitlines(keepends=True):
        line_start = pos
        content_end = line_start + len(line.rstrip())  # after last non-space char
        pos += len(line)
        stripped = line.strip()
        if in_fence:
            end = content_end
            if _FENCE_CLOSE_RE.match(line):
                in_fence = False
            continue
        if not stripped:  # blank line: close the current block
            if start is not None:
                blocks.append((start, end))
                start = None
            continue
        if start is None:
            start = line_start
        end = content_end
        if _FENCE_OPEN_RE.match(line):  # opens a fence (its close is handled above)
            in_fence = True
    if start is not None:
        blocks.append((start, end))
    return blocks


def _first_line(md_text: str, s: int, e: int) -> str:
    for ln in md_text[s:e].splitlines():
        if ln.strip():
            return ln
    return ""


def _merge_list_blocks(md_text: str, raws: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Fold adjacent list blocks (loose lists split by blank lines) back into
    one span, so a rendered <ul>/<ol> maps to a single anchor block."""
    out: list[list] = []
    for s, e in raws:
        is_list = bool(_LIST_RE.match(_first_line(md_text, s, e)))
        if out and is_list and out[-1][2]:
            out[-1][1] = e
        else:
            out.append([s, e, is_list])
    return [(s, e) for s, e, _ in out]


def _is_content_block(text: str) -> bool:
    """A block that renders to a visible top-level element. Marker-only blocks,
    stray HTML comments and link-reference definitions produce no element and so
    must not consume an anchor index (else bubbles drift off their blocks)."""
    t = re.sub(r"<!--.*?-->", "", strip_comment_markers(text), flags=re.S)
    lines = [ln for ln in t.splitlines() if ln.strip()]
    if not lines:
        return False
    if all(_LINKDEF_RE.match(ln) for ln in lines):
        return False
    return True


def analyze_comments(md_text: str) -> dict:
    """Parse cowrite markers out of the source for the editor UI.

    Returns ``{"comments": [...], "blocks": [...]}`` where each comment carries
    its author, text, source char offsets and the index of the content block it
    anchors to, and ``blocks[i]["end"]`` is the char offset at the end of the
    i-th content block (where a new comment for that block is appended). Indices
    line up with the preview's top-level elements (see ``render_fragment``).
    """
    raws = _merge_list_blocks(md_text, _raw_blocks(md_text))
    content_spans: list[tuple[int, int]] = []
    raw_to_content: list[int | None] = []
    for s, e in raws:
        if _is_content_block(md_text[s:e]):
            raw_to_content.append(len(content_spans))
            content_spans.append((s, e))
        else:
            raw_to_content.append(None)

    def content_for(offset: int) -> int:
        ridx = None
        for i, (s, e) in enumerate(raws):
            if s <= offset:
                ridx = i
            else:
                break
        if ridx is None:
            ridx = 0 if raws else -1
        for j in range(ridx, -1, -1):  # nearest content block at or before
            if 0 <= j < len(raw_to_content) and raw_to_content[j] is not None:
                return raw_to_content[j]
        for j in range(ridx, len(raws)):  # else the first one after
            if raw_to_content[j] is not None:
                return raw_to_content[j]
        return -1

    comments = [
        {
            "id": cid,
            "author": (m.group(1).strip() or "daniel"),
            "text": m.group(2).strip(),
            "block": content_for(m.start()),
            "start": m.start(),
            "end": m.end(),
        }
        for cid, m in enumerate(_COMMENT_RE.finditer(md_text))
    ]
    return {"comments": comments, "blocks": [{"end": e} for _, e in content_spans]}


def _pygments_css() -> str:
    try:
        from pygments.formatters import HtmlFormatter

        return HtmlFormatter().get_style_defs(".codehilite")
    except Exception:
        return ""


# Note typography lives in notes.css — the sheet shared with the JARVIS
# lab-notes site, so the preview matches what the site publishes. The block
# below is only preview-pane layout + bits notes.css doesn't own.
_NOTES_CSS = (Path(__file__).parent / "notes.css").read_text()

_PREVIEW_CSS = """
.preview { padding: 1.6rem 1.4rem 4rem; }
.preview .codehilite { border-radius: 8px; }
"""

# Docs-style anchored comments: a highlight on the anchor block plus a margin
# bubble in a reserved right rail. Appended after _PREVIEW_CSS so the reserved
# padding-right wins over its padding shorthand.
_COMMENTS_CSS = """
.preview { position: relative; padding-right: 248px; }
.cowrite-anchored { background: rgba(255,212,0,.20); border-radius: 3px;
  box-shadow: -3px 0 0 rgba(255,193,7,.95); }
.cowrite-bubble { position: absolute; right: 8px; width: 216px; font-size: 12.5px;
  background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: .5rem .6rem;
  box-shadow: 0 1px 4px rgba(0,0,0,.12); }
.cowrite-bubble .who { font-weight: 600; color: #0969da; font-size: 11.5px; }
.cowrite-bubble .body { margin: .25rem 0 .45rem; white-space: pre-wrap; word-wrap: break-word; }
.cowrite-bubble button.resolve { font: inherit; font-size: 11.5px; cursor: pointer; color: #1f2328;
  background: #f6f8fa; border: 1px solid rgba(31,35,40,.15); border-radius: 6px; padding: .15rem .5rem; }
.cowrite-bubble button.resolve:hover { background: #eef1f4; }
.cowrite-comment-btn { position: fixed; z-index: 50; font: inherit; font-size: 12.5px; font-weight: 600;
  cursor: pointer; color: #fff; background: #1f883d; border: 1px solid rgba(31,35,40,.15);
  border-radius: 6px; padding: .2rem .55rem; box-shadow: 0 1px 4px rgba(0,0,0,.2); }
@media (prefers-color-scheme: dark) {
  .cowrite-anchored { background: rgba(255,212,0,.15); }
  .cowrite-bubble { background: #161b22; border-color: #30363d; }
  .cowrite-bubble .who { color: #4493f8; }
  .cowrite-bubble button.resolve { color: #e6edf3; background: #21262d; border-color: #30363d; }
}
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
  <div class="pane view"><div class="preview note" id="preview">__PREVIEW__</div></div>
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
// Anchored comments: the marker author, plus the comments + content-block end
// offsets parsed from the on-disk source (refreshed on every save / sync).
let COWRITE_AUTHOR = '__AUTHOR__';
let COWRITE_COMMENTS = __COMMENTS__;
let COWRITE_BLOCKS = __BLOCKS__;

function setStatus(text, cls) { status.textContent = text; status.className = 'status' + (cls ? ' ' + cls : ''); }
function markDirty() { if (src.value !== clean) setStatus('● unsaved', 'dirty'); else setStatus('saved', 'saved'); }

function applyRendered(data) {
  preview.innerHTML = data.html;
  if (data.comments) { applyComments(data.comments, data.blocks); }
  if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetClear && MathJax.typesetClear([preview]);
    MathJax.typesetPromise([preview]).then(drawComments);
  }
}

async function save(baseRev) {
  if (saving || src.value === clean) { return; }
  saving = true; saveBtn.disabled = true; setStatus('saving…');
  try {
    const r = await fetch('save', { method: 'POST',
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
    const r = await fetch('api/state');
    const state = await r.json();
    if (pollFails >= 3) { markDirty(); }  // recovered: clear the lost-connection status
    pollFails = 0;
    if (state.rev !== rev) {
      if (src.value === clean) {
        const doc = await (await fetch('api/doc')).json();
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
    const r = await fetch('revert', { method: 'POST' });
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

// ---- Anchored comments (Docs-style review via inline cowrite[...] markers) ----
// The markers live in the markdown itself, so insert = splice a marker into the
// source and Save; resolve = delete the marker and Save. Both flow through the
// normal /save path (X-Base-Rev), so a concurrent AI write 409s like any save.
const commentBtn = document.createElement('button');
commentBtn.className = 'cowrite-comment-btn';
commentBtn.textContent = '💬 Comment';
commentBtn.style.display = 'none';
document.body.appendChild(commentBtn);
let pendingBlock = -1;

function hideCommentBtn() { commentBtn.style.display = 'none'; }

function topLevelBlockOf(node) {  // climb to the direct child of the preview
  let el = (node && node.nodeType === 3) ? node.parentNode : node;
  while (el && el.parentNode !== preview) { el = el.parentNode; }
  return (el && el.parentNode === preview) ? el : null;
}

function updateCommentBtn() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) { hideCommentBtn(); return; }
  const range = sel.getRangeAt(0);
  const el = topLevelBlockOf(range.commonAncestorContainer);
  if (!el || el.classList.contains('cowrite-bubble')) { hideCommentBtn(); return; }
  pendingBlock = Array.prototype.indexOf.call(preview.children, el);
  const rect = range.getBoundingClientRect();
  commentBtn.style.top = Math.max(4, rect.top - 34) + 'px';
  commentBtn.style.left = rect.left + 'px';
  commentBtn.style.display = 'block';
}

preview.addEventListener('mouseup', () => setTimeout(updateCommentBtn, 0));
preview.addEventListener('scroll', hideCommentBtn);
commentBtn.addEventListener('mousedown', (e) => e.preventDefault());  // keep selection
commentBtn.addEventListener('click', () => {
  const block = pendingBlock;
  hideCommentBtn();
  if (src.value !== clean) { setStatus('save your edits before commenting', 'err'); return; }
  const text = window.prompt('Comment on the selected block:');
  if (text && text.trim()) { insertComment(block, text); }
});

function sanitizeComment(t) {  // keep the marker one-line and un-closable
  return (t || '').replace(/\\s+/g, ' ').replace(/--+>/g, '—>').trim();
}

function insertComment(block, text) {
  text = sanitizeComment(text);
  if (!text) { return; }
  const at = (block >= 0 && COWRITE_BLOCKS[block]) ? COWRITE_BLOCKS[block].end : src.value.length;
  const marker = ' <!-- cowrite[' + COWRITE_AUTHOR + ']: ' + text + ' -->';
  src.value = src.value.slice(0, at) + marker + src.value.slice(at);
  markDirty();
  save();
}

function resolveComment(c) {  // resolve = delete the marker, then Save
  if (src.value !== clean) { setStatus('save your edits before resolving', 'err'); return; }
  let a = c.start;
  if (a > 0 && src.value[a - 1] === ' ') { a -= 1; }  // eat the joining space
  src.value = src.value.slice(0, a) + src.value.slice(c.end);
  markDirty();
  save();
}

function applyComments(comments, blocks) {
  COWRITE_COMMENTS = comments || [];
  COWRITE_BLOCKS = blocks || [];
  drawComments();
}

function drawComments() {
  preview.querySelectorAll('.cowrite-bubble').forEach((b) => b.remove());
  preview.querySelectorAll('.cowrite-anchored').forEach((e) => e.classList.remove('cowrite-anchored'));
  let lastBottom = -1;
  COWRITE_COMMENTS.forEach((c) => {
    const anchor = (c.block >= 0) ? preview.children[c.block] : null;
    if (!anchor) { return; }
    anchor.classList.add('cowrite-anchored');
    const bubble = document.createElement('div');
    bubble.className = 'cowrite-bubble';
    const who = document.createElement('div');
    who.className = 'who';
    who.textContent = '@' + c.author;
    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = c.text;
    const btn = document.createElement('button');
    btn.className = 'resolve';
    btn.textContent = 'Resolve';
    btn.addEventListener('click', () => resolveComment(c));
    bubble.appendChild(who);
    bubble.appendChild(body);
    bubble.appendChild(btn);
    bubble.addEventListener('click', (e) => { if (e.target !== btn) { anchor.scrollIntoView({ block: 'center' }); } });
    preview.appendChild(bubble);
    let top = anchor.offsetTop;
    if (top < lastBottom + 8) { top = lastBottom + 8; }  // stack, don't overlap
    bubble.style.top = top + 'px';
    lastBottom = top + bubble.offsetHeight;
  });
}

window.addEventListener('resize', () => { if (COWRITE_COMMENTS.length) { drawComments(); } });
applyComments(COWRITE_COMMENTS, COWRITE_BLOCKS);
</script>
</body></html>
"""


def build_page(md_text: str, title: str, disk_path: str, rev: str, author: str = "daniel") -> str:
    """Build the full editor HTML page for a draft."""
    import json

    author = re.sub(r"[^\w.-]", "", author) or "daniel"
    data = analyze_comments(md_text)
    # `<` -> < keeps a comment body containing "</script>" from breaking out.
    comments_json = json.dumps(data["comments"]).replace("<", "\\u003c")
    blocks_json = json.dumps(data["blocks"]).replace("<", "\\u003c")
    css = (_CHROME_CSS + "\n" + _NOTES_CSS + "\n" + _PREVIEW_CSS + "\n"
           + _COMMENTS_CSS + "\n" + _pygments_css())
    return (
        _PAGE.replace("__CSS__", css)
        .replace("__TITLE__", _html.escape(title))
        .replace("__PATH__", _html.escape(disk_path))
        .replace("__REV__", rev)
        .replace("__MD__", _html.escape(md_text))
        .replace("__PREVIEW__", render_fragment(md_text))
        # These go last so a comment/block payload can't reintroduce an earlier
        # placeholder, and their values are JSON/sanitized (safe to inject).
        .replace("__AUTHOR__", author)
        .replace("__COMMENTS__", comments_json)
        .replace("__BLOCKS__", blocks_json)
    )
