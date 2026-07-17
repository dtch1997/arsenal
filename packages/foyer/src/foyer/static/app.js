/* foyer frontend: session sidebar + xterm terminal + plots/notes panel. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  sessions: [], active: null, lastJson: "",
  notesTimer: null, notesSeq: 0, plotsVisible: false, dragging: null,
  config: { workspace: "", command: "" }, renaming: null, warmed: false,
};
fetch("/api/config").then((r) => r.json()).then((c) => { state.config = c; })
  .catch(() => {});

/* --- terminals: one kept-alive entry per thread --------------------------- */
/* Switching threads shows/hides live terminals instead of reconnecting, so a
   switch is instant after the first visit. The first ~MAX_TERMS threads are
   pre-warmed on load; least-recently-used entries are evicted past the cap. */
const MAX_TERMS = 8;
const terms = new Map(); // name -> {name, term, fit, slot, ws, alive, lastUsed}
let useClock = 0;

function makeEntry(name) {
  const slot = document.createElement("div");
  slot.className = "term-slot";
  $("terminal").appendChild(slot);
  const term = new Terminal({
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    fontSize: 13,
    scrollback: 8000,
    cursorBlink: true,
    theme: {
      background: "#0e1116", foreground: "#d7dde6", cursor: "#57b0a3",
      selectionBackground: "#2a4a45",
    },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(slot);
  const entry = { name, term, fit, slot, ws: null, alive: false, lastUsed: 0 };
  term.onData((d) => {
    if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
      entry.ws.send(JSON.stringify({ type: "input", data: d }));
    }
  });
  connectEntry(entry);
  terms.set(name, entry);
  return entry;
}

function connectEntry(entry) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(
    `${proto}://${location.host}/ws?target=${encodeURIComponent(entry.name)}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { entry.alive = true; fitEntry(entry); };
  ws.onmessage = (ev) => entry.term.write(new Uint8Array(ev.data));
  ws.onclose = () => {
    entry.alive = false;
    if (state.active === entry.name) $("disconnected").classList.remove("hidden");
  };
  entry.ws = ws;
}

function fitEntry(entry) {
  try { entry.fit.fit(); } catch (e) { return; }
  if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
    entry.ws.send(JSON.stringify(
      { type: "resize", cols: entry.term.cols, rows: entry.term.rows }));
  }
}

function disposeEntry(entry) {
  terms.delete(entry.name);
  if (entry.ws) { entry.ws.onclose = null; try { entry.ws.close(); } catch (e) {} }
  entry.term.dispose();
  entry.slot.remove();
}

function evictOverCap() {
  while (terms.size > MAX_TERMS) {
    let victim = null;
    for (const e of terms.values()) {
      if (e.name === state.active) continue;
      if (!victim || e.lastUsed < victim.lastUsed) victim = e;
    }
    if (!victim) return;
    disposeEntry(victim);
  }
}

function attach(name) {
  state.active = name;
  document.title = `${name} — foyer`;
  $("placeholder").classList.add("hidden");
  $("disconnected").classList.add("hidden");
  let entry = terms.get(name);
  if (entry && !entry.alive && entry.ws &&
      entry.ws.readyState !== WebSocket.CONNECTING) {
    disposeEntry(entry); // died while hidden — rebuild silently
    entry = null;
  }
  if (!entry) { entry = makeEntry(name); evictOverCap(); }
  entry.lastUsed = ++useClock;
  for (const e of terms.values()) {
    e.slot.classList.toggle("active", e === entry);
  }
  fitEntry(entry);
  entry.term.focus();
  renderSessions();
  loadNotes(name);
  loadPlots();
}

function prewarm() {
  const names = state.sessions.slice(0, MAX_TERMS).map((s) => s.name);
  let delay = 0;
  for (const name of names) {
    if (terms.has(name)) continue;
    delay += 150; // stagger the attach burst a little
    setTimeout(() => {
      if (!terms.has(name) && terms.size < MAX_TERMS) makeEntry(name);
    }, delay);
  }
}

new ResizeObserver(() => { for (const e of terms.values()) fitEntry(e); })
  .observe($("term-holder"));

$("reconnect").onclick = () => {
  const name = state.active;
  if (!name) return;
  const entry = terms.get(name);
  if (entry) disposeEntry(entry);
  attach(name);
};

/* --- sidebar ------------------------------------------------------------- */
function ago(epoch) {
  const s = Math.max(0, Date.now() / 1000 - epoch);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function renderSessions() {
  const box = $("sessions");
  box.innerHTML = "";
  for (const s of state.sessions) {
    const card = document.createElement("div");
    card.className = "card" + (s.name === state.active ? " active" : "");
    const title = s.title && s.title !== s.name ? s.title : s.command;
    const last = (s.preview || []).slice(-1)[0] || "";
    card.innerHTML = `
      <div class="row1">
        <span class="dot${s.agent ? " agent" : ""}"></span>
        <span class="name"></span>
        ${s.attached ? '<span class="badge">tty</span>' : ""}
        <span class="ago">${ago(s.activity)}</span>
      </div>
      <div class="meta"></div>
      <div class="snippet"></div>`;
    const nameEl = card.querySelector(".name");
    nameEl.textContent = s.name;
    nameEl.title = "double-click to rename";
    card.querySelector(".meta").textContent = `${s.dir}  ·  ${title}`;
    card.querySelector(".snippet").textContent = last;
    card.onclick = () => attach(s.name);
    nameEl.ondblclick = (e) => { e.stopPropagation(); startRename(nameEl, s.name); };
    wireDrag(card, s.name);
    box.appendChild(card);
  }
}

/* inline rename: double-click a thread's name, Enter commits, Esc cancels */
function startRename(nameEl, oldName) {
  if (state.renaming) return;
  state.renaming = oldName;
  const input = document.createElement("input");
  input.value = oldName;
  nameEl.textContent = "";
  nameEl.appendChild(input);
  input.focus();
  input.select();
  input.onclick = (e) => e.stopPropagation();
  const done = () => { state.renaming = null; state.lastJson = ""; renderSessions(); };
  input.onkeydown = async (e) => {
    if (e.key === "Escape") return done();
    if (e.key !== "Enter") return;
    const newName = input.value.trim();
    if (!newName || newName === oldName) return done();
    const r = await fetch(`/api/threads/${encodeURIComponent(oldName)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!r.ok) { input.title = await r.text(); input.style.borderColor = "#f87171"; return; }
    if (state.active === oldName) {
      state.active = newName;
      document.title = `${newName} — foyer`;
    }
    const entry = terms.get(oldName);
    if (entry) { // the attached client survives a tmux rename — just re-key
      terms.delete(oldName);
      entry.name = newName;
      terms.set(newName, entry);
    }
    state.sessions.forEach((s) => { if (s.name === oldName) s.name = newName; });
    done();
    refreshSessions();
  };
  input.onblur = done;
}

/* new-thread popover */
$("new-thread").onclick = () => {
  const dirBase = (state.config.workspace || "").split("/").pop() || "thread";
  const taken = new Set(state.sessions.map((s) => s.name));
  let n = 1;
  while (taken.has(`${dirBase}-${n}`)) n += 1;
  $("new-name").value = "";
  $("new-name").placeholder = `${dirBase}-${n}`;
  $("new-dir").value = state.config.workspace || "";
  $("new-error").textContent = "";
  $("new-form").classList.remove("hidden");
  $("new-thread").classList.add("hidden");
  $("new-name").focus();
};
function closeNewForm() {
  $("new-form").classList.add("hidden");
  $("new-thread").classList.remove("hidden");
}
$("new-cancel").onclick = closeNewForm;
$("new-form").onsubmit = async (e) => {
  e.preventDefault();
  $("new-error").textContent = "";
  const r = await fetch("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $("new-name").value.trim(),
      dir: $("new-dir").value.trim(),
    }),
  });
  if (!r.ok) { $("new-error").textContent = await r.text(); return; }
  const j = await r.json();
  closeNewForm();
  await refreshSessions();
  attach(j.name);
};

/* drag-and-drop reordering; the order is persisted server-side */
function wireDrag(card, name) {
  card.draggable = true;
  card.ondragstart = (e) => {
    state.dragging = name;
    e.dataTransfer.effectAllowed = "move";
    setTimeout(() => card.classList.add("dragging"), 0);
  };
  card.ondragend = () => {
    state.dragging = null;
    card.classList.remove("dragging");
    clearDropMarks();
  };
  card.ondragover = (e) => {
    if (!state.dragging || state.dragging === name) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    clearDropMarks();
    const before = e.offsetY < card.offsetHeight / 2;
    card.classList.add(before ? "drop-above" : "drop-below");
  };
  card.ondrop = async (e) => {
    e.preventDefault();
    const from = state.dragging;
    if (!from || from === name) return;
    const before = e.offsetY < card.offsetHeight / 2;
    clearDropMarks();
    const names = state.sessions.map((s) => s.name).filter((n) => n !== from);
    const at = names.indexOf(name) + (before ? 0 : 1);
    names.splice(at, 0, from);
    state.sessions.sort((a, b) => names.indexOf(a.name) - names.indexOf(b.name));
    state.lastJson = "";
    renderSessions();
    try {
      await fetch("/api/order", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
    } catch (err) { /* order re-syncs on next refresh */ }
  };
}
function clearDropMarks() {
  document.querySelectorAll(".drop-above,.drop-below")
    .forEach((c) => c.classList.remove("drop-above", "drop-below"));
}

async function refreshSessions() {
  if (state.dragging || state.renaming) return; // don't re-render mid-edit
  try {
    const r = await fetch("/api/sessions");
    if (!r.ok) return;
    const j = await r.text();
    if (j === state.lastJson) return;
    state.lastJson = j;
    state.sessions = JSON.parse(j).sessions;
    renderSessions();
    const live = new Set(state.sessions.map((s) => s.name));
    for (const e of [...terms.values()]) {
      if (!live.has(e.name)) disposeEntry(e); // session is gone
    }
    if (!state.warmed && state.sessions.length) {
      state.warmed = true;
      prewarm();
    }
  } catch (e) { /* transient */ }
}
$("refresh").onclick = refreshSessions;
setInterval(refreshSessions, 5000);
refreshSessions();

/* --- side panel ----------------------------------------------------------- */
function setPanel(open) {
  $("panel").classList.toggle("collapsed", !open);
  $("panel-open").classList.toggle("hidden", open);
  for (const e of terms.values()) fitEntry(e);
}
$("panel-toggle").onclick = () => setPanel(false);
$("panel-open").onclick = () => setPanel(true);

for (const btn of document.querySelectorAll(".tab")) {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(btn.dataset.tab).classList.add("active");
    state.plotsVisible = btn.dataset.tab === "plots";
    if (state.plotsVisible) loadPlots();
  };
}
state.plotsVisible = true;

/* --- plots ---------------------------------------------------------------- */
async function loadPlots() {
  if (!state.active) return;
  try {
    const r = await fetch(`/api/plots?session=${encodeURIComponent(state.active)}`);
    if (!r.ok) return;
    const j = await r.json();
    const rootInput = $("plot-root");
    if (document.activeElement !== rootInput) {
      rootInput.value = j.override ? j.root : "";
      rootInput.placeholder = j.default_root
        ? `plot directory (default: ${j.default_root})`
        : "plot directory (default: thread cwd)";
      rootInput.classList.toggle("override", j.override);
    }
    $("plots-meta").textContent = j.root ? `newest images under ${j.root}` : "";
    const grid = $("plots-grid");
    grid.innerHTML = "";
    if (!j.images.length) {
      grid.innerHTML = '<div style="color:var(--fg-dim);font-size:12px">no images near this thread’s cwd yet</div>';
      return;
    }
    for (const im of j.images) {
      const el = document.createElement("div");
      el.className = "plot-card";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = `/api/file?path=${encodeURIComponent(im.path)}`;
      const cap = document.createElement("div");
      cap.className = "cap";
      cap.textContent = im.rel;
      cap.title = im.rel;
      el.append(img, cap);
      el.onclick = () => {
        $("lightbox-img").src = img.src;
        $("lightbox").classList.remove("hidden");
      };
      grid.appendChild(el);
    }
  } catch (e) { /* transient */ }
}
async function setPlotRoot(root) {
  if (!state.active) return;
  const r = await fetch(`/api/plotroot/${encodeURIComponent(state.active)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root }),
  });
  if (!r.ok) {
    $("plots-meta").textContent = await r.text();
    return;
  }
  $("plot-root").blur();
  loadPlots();
}
$("plot-root").addEventListener("keydown", (e) => {
  if (e.key === "Enter") setPlotRoot($("plot-root").value.trim());
  if (e.key === "Escape") $("plot-root").blur();
});
$("plot-root-reset").onclick = () => setPlotRoot("");

$("lightbox").onclick = () => $("lightbox").classList.add("hidden");
setInterval(() => {
  if (state.plotsVisible && !$("panel").classList.contains("collapsed")) loadPlots();
}, 15000);

/* --- notes ---------------------------------------------------------------- */
async function loadNotes(name) {
  const seq = ++state.notesSeq;
  try {
    const r = await fetch(`/api/notes/${encodeURIComponent(name)}`);
    const j = await r.json();
    if (seq === state.notesSeq) { $("notes-text").value = j.text; $("notes-status").textContent = ""; }
  } catch (e) { /* transient */ }
}
$("notes-text").addEventListener("input", () => {
  if (!state.active) return;
  const name = state.active;
  $("notes-status").textContent = "…";
  clearTimeout(state.notesTimer);
  state.notesTimer = setTimeout(async () => {
    try {
      await fetch(`/api/notes/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: $("notes-text").value }),
      });
      $("notes-status").textContent = `saved ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      $("notes-status").textContent = "save failed — will retry on next edit";
    }
  }, 700);
});

/* start with the panel open on wide screens */
setPanel(window.innerWidth > 1100);
