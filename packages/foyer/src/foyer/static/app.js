/* foyer frontend: session sidebar + xterm terminal + plots/notes panel. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  sessions: [], active: null, ws: null, lastJson: "",
  notesTimer: null, notesSeq: 0, plotsVisible: false,
};

/* --- terminal ------------------------------------------------------------ */
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
term.open($("terminal"));

function sendResize() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }
}
new ResizeObserver(() => { try { fit.fit(); sendResize(); } catch (e) {} })
  .observe($("term-holder"));

term.onData((d) => {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "input", data: d }));
  }
});

function attach(name) {
  if (state.ws) { state.ws.onclose = null; state.ws.close(); state.ws = null; }
  state.active = name;
  document.title = `${name} — foyer`;
  $("placeholder").classList.add("hidden");
  $("disconnected").classList.add("hidden");
  term.reset();
  fit.fit();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?target=${encodeURIComponent(name)}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { sendResize(); term.focus(); };
  ws.onmessage = (ev) => term.write(new Uint8Array(ev.data));
  ws.onclose = () => { if (state.active === name) $("disconnected").classList.remove("hidden"); };
  state.ws = ws;
  renderSessions();
  loadNotes(name);
  loadPlots();
}
$("reconnect").onclick = () => state.active && attach(state.active);

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
    card.querySelector(".name").textContent = s.name;
    card.querySelector(".meta").textContent = `${s.dir}  ·  ${title}`;
    card.querySelector(".snippet").textContent = last;
    card.onclick = () => attach(s.name);
    box.appendChild(card);
  }
}

async function refreshSessions() {
  try {
    const r = await fetch("/api/sessions");
    if (!r.ok) return;
    const j = await r.text();
    if (j === state.lastJson) return;
    state.lastJson = j;
    state.sessions = JSON.parse(j).sessions;
    renderSessions();
  } catch (e) { /* transient */ }
}
$("refresh").onclick = refreshSessions;
setInterval(refreshSessions, 5000);
refreshSessions();

/* --- side panel ----------------------------------------------------------- */
function setPanel(open) {
  $("panel").classList.toggle("collapsed", !open);
  $("panel-open").classList.toggle("hidden", open);
  try { fit.fit(); sendResize(); } catch (e) {}
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
