// LinguaHaru Web frontend — talks to the FastAPI backend.
const $ = (id) => document.getElementById(id);

// Inline 1px-stroke icons used by JS-generated markup (no emoji).
const ICON = {
  sun:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>',
  check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 6.5"/></svg>',
  cross:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  chevron:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
  download:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/></svg>',
};
// Per-plugin glyphs for the plaza cards (mirrors the Qt OptionalPluginCard icons).
const PLUGIN_ICON = {
  "PDF":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 16h6"/></svg>',
  "Image OCR":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5-5L5 20"/></svg>',
  "漫画翻译":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h5M8 11h8M8 15h6"/></svg>',
  "Video/Audio":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/></svg>',
  "Real-Time Voice":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>',
  "翻译语音输入":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11v2M7 8v8M11 5v14M15 8v8M19 11v2"/></svg>',
};
const pill = (cls, label, icon) => `<span class="pill ${cls}">${icon || ""}${label}</span>`;

// Empty-state + loading-skeleton helpers (no emoji; 1px line glyphs).
const EICON = {
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13l3-7h12l3 7"/><path d="M3 13v5a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-5"/><path d="M3 13h5l1.5 2.5h5L21 13"/></svg>',
  files: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h5l4 4v11a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/><path d="M5 7v12a1 1 0 0 0 1 1h8"/></svg>',
};
const _esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// icon is a trusted SVG constant; title/sub are text (sub may be a server error
// message) so they are HTML-escaped to avoid injection.
const emptyState = (icon, title, sub) =>
  `<div class="empty-state"><div class="es-icon">${icon}</div>` +
  `<div class="es-title">${_esc(title)}</div><div class="es-sub">${_esc(sub)}</div></div>`;
function tableSkeleton(t, n) {
  t.innerHTML = "";
  for (let i = 0; i < (n || 5); i++)
    t.innerHTML += '<tr><td style="border:none;padding:5px 2px"><div class="skeleton"><div class="sk"></div></div></td></tr>';
}
let BOOT = null;
let currentFiles = [];
let currentTask = null;
const MEDIA_EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"];
const IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".bmp", ".webp"];
const VIDEO_EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm"];  // extract audio client-side

// ---- ffmpeg.wasm: extract the audio track in the browser so we upload a few
// MB of audio instead of a multi-GB video. Falls back to direct upload. ----
let _ffmpeg = null;
function loadScript(src) {
  return new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = src; s.crossOrigin = "anonymous";
    s.onload = res; s.onerror = () => rej(new Error("failed to load " + src));
    document.head.appendChild(s);
  });
}
async function getFfmpeg() {
  if (_ffmpeg) return _ffmpeg;
  await loadScript("https://unpkg.com/@ffmpeg/ffmpeg@0.12.10/dist/umd/ffmpeg.js");
  await loadScript("https://unpkg.com/@ffmpeg/util@0.12.1/dist/umd/util.js");
  const { FFmpeg } = window.FFmpegWASM;
  const { toBlobURL } = window.FFmpegUtil;
  const ff = new FFmpeg();
  const base = "https://unpkg.com/@ffmpeg/core@0.12.6/dist/umd";
  await ff.load({
    coreURL: await toBlobURL(`${base}/ffmpeg-core.js`, "text/javascript"),
    wasmURL: await toBlobURL(`${base}/ffmpeg-core.wasm`, "application/wasm"),
  });
  _ffmpeg = ff;
  return ff;
}
async function extractAudio(file, onProgress) {
  const ff = await getFfmpeg();
  const { fetchFile } = window.FFmpegUtil;
  const inName = "in_" + file.name.replace(/[^\w.]/g, "_");
  // ffmpeg.wasm reports decode progress as a 0..1 fraction via the "progress"
  // event — surface it so the user sees the extraction advancing (it can take a
  // while for a long video) instead of a frozen UI.
  let handler = null;
  if (onProgress) {
    handler = ({ progress }) => onProgress(Math.max(0, Math.min(1, progress || 0)));
    ff.on("progress", handler);
  }
  try {
    await ff.writeFile(inName, await fetchFile(file));
    await ff.exec(["-i", inName, "-vn", "-ac", "1", "-ar", "16000", "out.wav"]);
    const data = await ff.readFile("out.wav");
    try { await ff.deleteFile(inName); await ff.deleteFile("out.wav"); } catch (e) { /* ignore */ }
    const stem = file.name.replace(/\.[^.]+$/, "");
    return new File([data.buffer], stem + ".wav", { type: "audio/wav" });
  } finally {
    if (handler) { try { ff.off("progress", handler); } catch (e) { /* ignore */ } }
  }
}

// Drive the run dashboard's ring/bar during the in-browser audio-extraction
// phase (before the server task exists), so it's not a dead 0% until upload.
function setExtractProgress(frac, desc) {
  const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
  $("progress-bar").style.width = pct + "%";
  $("prog-ring").style.setProperty("--p", (pct * 3.6) + "deg");
  $("prog-pct").textContent = pct + "%";
  if (desc != null) $("progress-desc").textContent = desc;
}

// LAN admin token: kept in memory only (never localStorage), entered via a
// password field (never prompt(), which would render it in cleartext).
let _adminToken = "";
function askAdminPassword() {
  return new Promise((resolve) => {
    const m = $("admin-modal");
    if (!m) { resolve(null); return; }
    $("admin-pw").value = "";
    m.hidden = false;
    $("admin-pw").focus();
    const done = (val) => { m.hidden = true; $("admin-ok").onclick = null; $("admin-cancel").onclick = null; resolve(val); };
    $("admin-ok").onclick = () => done($("admin-pw").value);
    $("admin-cancel").onclick = () => done(null);
  });
}
async function api(path, opts) {
  opts = opts || {};
  if (_adminToken) opts.headers = Object.assign({ "X-Admin-Token": _adminToken }, opts.headers || {});
  let r = await fetch(path, opts);
  // 401 from an admin endpoint -> ask for the LAN admin password and retry.
  if (r.status === 401) {
    const pw = await askAdminPassword();
    if (pw) {
      _adminToken = pw;
      opts.headers = Object.assign({ "X-Admin-Token": pw }, opts.headers || {});
      r = await fetch(path, opts);
    }
  }
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

function fillSelect(sel, items, value) {
  sel.innerHTML = "";
  for (const it of items) {
    const o = document.createElement("option");
    if (typeof it === "object") { o.value = it.id; o.textContent = it.label; }
    else { o.value = it; o.textContent = it; }
    sel.appendChild(o);
  }
  if (value != null) sel.value = value;
}

function modelsForMode(online) {
  return online ? BOOT.online_models : BOOT.local_models;
}

// The media STT picker offers ONLY downloaded models (the user installs them in
// Settings -> Model Management). Refreshed on load and whenever a media file is
// picked, so newly-installed models appear without a reload.
async function refreshSttPicker() {
  const sel = $("stt-model");
  if (!sel) return;
  const cur = BOOT.config && BOOT.config.stt_model;
  let items;
  try {
    const d = await api("/api/models");
    items = (d.stt || []).filter((m) => m.downloaded).map((m) => ({ id: m.id, label: m.label }));
  } catch (e) {
    items = BOOT.stt_models || [];   // endpoint unavailable -> degrade to full list
  }
  fillSelect(sel, items, items.some((m) => m.id === cur) ? cur : (items[0] && items[0].id));
  // If the configured model isn't downloaded, the picker fell back to another —
  // persist it so the BACKEND transcribes with what the UI shows (not a deleted one).
  if (sel.value && sel.value !== cur) {
    if (BOOT.config) BOOT.config.stt_model = sel.value;
    saveConfig({ stt_model: sel.value });
  }
}

// Online vs offline is decided by the ACTIVE interface (set in Interface
// Management), not a Settings checkbox. activateIface keeps this in sync.
function useOnline() {
  return !!(BOOT.config && BOOT.config.default_online);
}

// Top-right transient notification (e.g. after a model install/delete).
function toast(msg, kind) {
  let wrap = $("lh-toasts");
  if (!wrap) { wrap = document.createElement("div"); wrap.id = "lh-toasts"; document.body.appendChild(wrap); }
  const t = document.createElement("div");
  t.className = "lh-toast" + (kind ? " " + kind : "");
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => { t.classList.add("hide"); setTimeout(() => t.remove(), 400); }, 3200);
}

// Model Management: per-model install / delete / use, grouped by model type.
// OCR uses the "Image OCR" plugin, STT the "Video/Audio" plugin (the video
// subtitle STT — real-time / quick voice pick their model on the Plugins page).
async function refreshModels() {
  if (!$("models-dir")) return;
  let d;
  try { d = await api("/api/models"); } catch (e) { return; }  // server_mode etc.
  $("models-dir").value = d.dir || "";
  renderModelRows($("models-ocr"), "Image OCR", d.ocr || []);
  renderModelRows($("models-stt"), "Video/Audio", d.stt || []);
}

function renderModelRows(host, plugin, states) {
  if (!host) return;
  host.replaceChildren();
  states.forEach((s) => host.appendChild(modelRow(plugin, s)));
}

function modelRow(plugin, s) {
  const row = document.createElement("div");
  row.className = "model-row";

  const main = document.createElement("div"); main.className = "model-row-main";
  const name = document.createElement("span"); name.className = "model-row-name";
  name.textContent = _label(s.label, s.label);
  main.appendChild(name);
  (s.tags || []).forEach((t) => {
    const c = document.createElement("span");
    c.className = "model-tag" + (t === "Tag Recommended" ? " rec" : "");
    c.textContent = _label(t, t);
    main.appendChild(c);
  });

  const size = document.createElement("span");
  size.className = "model-row-size";
  size.textContent = (s.size || "") + (s.vram ? ` · ${s.vram}` : "");

  const act = document.createElement("div"); act.className = "model-row-act";
  // Per-model parameter entry (between capacity and Install/Delete) for models
  // that expose tunable params (STT only).
  if (s.params && s.params.length) {
    const p = document.createElement("button"); p.className = "mini";
    p.textContent = _label("Parameters", "参数");
    p.onclick = () => openModelParams(s);
    act.appendChild(p);
  }
  if (!s.downloaded) {
    const b = document.createElement("button"); b.className = "mini";
    b.textContent = _label("Install", "安装");
    b.onclick = () => installModel(plugin, s, b);
    act.appendChild(b);
  } else {
    const del = document.createElement("button"); del.className = "mini danger";
    del.textContent = _label("Delete", "删除");
    del.onclick = () => deleteModel(plugin, s, del); act.appendChild(del);
  }
  row.append(main, size, act);
  return row;
}

// Modal editor for a model's tunable STT params (switch / number per spec).
function openModelParams(s) {
  const cur = s.param_values || {};
  const back = document.createElement("div");
  back.className = "modal-overlay";
  const box = document.createElement("div"); box.className = "modal";
  const h = document.createElement("h3"); h.textContent = _label("Parameters", "参数");
  const sub = document.createElement("p"); sub.className = "model-param-sub";
  sub.textContent = _label(s.label, s.label);
  box.append(h, sub);

  const inputs = {};
  s.params.forEach((spec) => {
    const row = document.createElement("label"); row.className = "model-param-row";
    const lbl = document.createElement("span"); lbl.textContent = _label(spec.label, spec.label);
    let inp;
    if (spec.type === "bool") {
      inp = document.createElement("input"); inp.type = "checkbox";
      inp.checked = !!(spec.key in cur ? cur[spec.key] : spec.default);
    } else {
      inp = document.createElement("input"); inp.type = "number";
      inp.min = spec.min; inp.max = spec.max; inp.step = spec.step;
      inp.value = (spec.key in cur ? cur[spec.key] : spec.default);
    }
    inputs[spec.key] = { inp, spec };
    row.append(lbl, inp); box.appendChild(row);
  });

  const acts = document.createElement("div"); acts.className = "modal-actions";
  const reset = document.createElement("button");
  reset.textContent = _label("Reset to defaults", "恢复默认");
  reset.onclick = () => s.params.forEach((spec) => {
    const { inp } = inputs[spec.key];
    if (spec.type === "bool") inp.checked = !!spec.default; else inp.value = spec.default;
  });
  const cancel = document.createElement("button");
  cancel.textContent = _label("Cancel", "取消");
  cancel.onclick = () => back.remove();
  const save = document.createElement("button"); save.className = "primary";
  save.textContent = _label("Save", "保存");
  save.onclick = async () => {
    const values = {};
    Object.entries(inputs).forEach(([k, { inp, spec }]) => {
      values[k] = spec.type === "bool" ? inp.checked : Number(inp.value);
    });
    try {
      const r = await api("/api/models/params", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: s.id, values }),
      });
      s.param_values = { ...(s.param_values || {}), ...values };
      toast(_label("Parameters saved", "参数已保存"), "ok");
      back.remove();
    } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
  };
  acts.append(reset, cancel, save); box.appendChild(acts);
  back.appendChild(box);
  back.onclick = (e) => { if (e.target === back) back.remove(); };
  document.body.appendChild(back);
}

async function installModel(plugin, s, btn) {
  btn.disabled = true; btn.textContent = _label("Downloading", "下载中…");
  try {
    await api("/api/modules/model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: plugin, model_id: s.id }),
    });
  } catch (e) {
    btn.disabled = false; btn.textContent = _label("Install", "安装");
    toast((e.message || "failed").slice(-160), "bad"); return;
  }
  const poll = setInterval(async () => {
    let st; try { st = await api("/api/modules/status?name=" + encodeURIComponent(plugin)); } catch { return; }
    if (st.status === "running") return;
    clearInterval(poll);
    if (st.status === "done") toast(_label("Model Installed", "模型已安装") + "：" + s.label, "ok");
    else toast((st.output || "failed").slice(-160), "bad");
    refreshModels();
  }, 1500);
}

async function deleteModel(plugin, s, btn) {
  if (!confirm(_label("Delete Model Confirm", "确定删除该模型的本地文件吗？"))) return;
  btn.disabled = true; btn.textContent = _label("Deleting", "删除中…");
  try {
    await api("/api/models/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin, model_id: s.id }),
    });
    toast(_label("Model Deleted", "模型已删除") + "：" + s.label, "ok");
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
  refreshModels();
}

// ----- tabs -----
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    const tab = t.dataset.tab;
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    // "Live voice" is its own nav item but reuses the translate panel's live
    // subpane (so it shows as a separate page like the Qt app, without moving
    // the markup).
    const panelName = tab === "live" ? "translate" : tab;
    document.querySelector(`.panel[data-panel="${panelName}"]`).classList.add("active");
    if (tab === "live") showTranslateSub("live");
    else if (tab === "translate") showTranslateSub("doc");
    if (tab === "quick") onQuickShow();
    if (tab === "interface") loadInterfaces();
    if (tab === "glossary") loadGlossaryTable($("glossary-edit-select").value);
    if (tab === "proofread") loadProofreadDocs();
    if (tab === "history") loadHistory();
    document.body.classList.remove("nav-open");
  };
});

function showTranslateSub(sub) {
  document.querySelectorAll('.panel[data-panel="translate"] .subpane').forEach((x) => x.classList.remove("active"));
  const el = document.querySelector(`.panel[data-panel="translate"] .subpane[data-sub="${sub}"]`);
  if (el) el.classList.add("active");
  // Heading reflects the active sub: live -> 实时语音, doc -> 文件翻译 (data-i18n
  // kept in sync so it stays correct after a language switch).
  const h2 = document.querySelector('.panel[data-panel="translate"] .page-head h2');
  const p = document.querySelector('.panel[data-panel="translate"] .page-head p');
  if (h2) { h2.dataset.i18n = sub === "live" ? "Real-Time Voice" : "File Translation";
            h2.textContent = _label(h2.dataset.i18n, h2.textContent); }
  if (p) { p.dataset.i18n = sub === "live" ? "Real-Time Voice Subtitle" : "Translate Page Subtitle";
           p.textContent = _label(p.dataset.i18n, p.textContent); }
  document.querySelectorAll('#translate-mode .seg').forEach(
    (s) => s.classList.toggle("active", s.dataset.sub === sub));
}

// ----- interface management (mirrors the Qt Interface page) -----
let _ifaceActive = "";
async function loadInterfaces() {
  let d;
  try { d = await api("/api/interfaces"); } catch { return; }
  _ifaceActive = d.active || "";
  $("iface-active").textContent = d.active ? "✓ " + d.active : "";
  renderIfaceGroup("iface-local", (d.local || []).map((n) => ({ name: n, sub: "Ollama / LM Studio", online: false })));
  renderIfaceGroup("iface-official", (d.online || []).filter((i) => i.official).map((i) => ({ name: i.name, sub: i.model, online: true })));
  renderIfaceGroup("iface-custom", (d.online || []).filter((i) => !i.official).map((i) => ({ name: i.name, sub: i.model, online: true })));
  refreshGoogleKeyPlaceholder();
}
function renderIfaceGroup(id, items) {
  const box = $(id);
  if (!box) return;
  box.innerHTML = "";
  if (!items.length) { box.innerHTML = '<div class="muted">—</div>'; return; }
  for (const it of items) {
    const card = document.createElement("div");
    card.className = "iface-card" + (it.name === _ifaceActive ? " active" : "");
    // Build with textContent (NOT innerHTML) — the interface name/model are
    // user-entered strings and would otherwise allow stored XSS.
    const nameEl = document.createElement("div"); nameEl.className = "if-name"; nameEl.textContent = it.name;
    const subEl = document.createElement("div"); subEl.className = "if-sub mono"; subEl.textContent = it.sub || "";
    card.append(nameEl, subEl);
    if (it.name === _ifaceActive) {
      const badge = document.createElement("span"); badge.className = "if-badge"; badge.textContent = "✓";
      card.append(badge);
    }
    card.onclick = () => activateIface(it.name, it.online);
    // Online: full config. Offline/local: open config too so its thread count
    // can be set (default 4); other fields are harmless for local models.
    card.ondblclick = () => openIfaceConfig(it.name);
    box.appendChild(card);
  }
}
async function activateIface(name, online) {
  try {
    await api("/api/interface/activate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, online }) });
  } catch { return; }
  BOOT.config.default_online = online;            // sync the translate dropdown
  if (online) BOOT.config.default_online_model = name;
  fillSelect($("model"), modelsForMode(online), online ? name : null);
  if (typeof refreshLiveModel === "function") refreshLiveModel();
  loadInterfaces();
}
let _ifaceEditing = null;
async function openIfaceConfig(name) {
  _ifaceEditing = name;
  $("iface-modal-title").textContent = name || "添加接口";
  $("if-name").value = name || ""; $("if-name").disabled = !!name;
  $("if-delete").hidden = !name;
  $("if-base").value = ""; $("if-model").value = ""; $("if-key").value = "";
  $("if-temp").value = ""; $("if-topp").value = ""; $("if-key").placeholder = "在此输入 API 密钥";
  $("if-rpm").value = ""; $("if-thread").value = ""; $("if-retries").value = "";
  if (name) {
    try {
      const c = await api("/api/interface/config?name=" + encodeURIComponent(name));
      $("if-base").value = c.base_url || ""; $("if-model").value = c.model || "";
      $("if-temp").value = c.temperature ?? ""; $("if-topp").value = c.top_p ?? "";
      $("if-rpm").value = c.rpm ?? ""; $("if-thread").value = c.thread_count ?? "";
      $("if-retries").value = c.max_retries ?? "";
      $("if-key").placeholder = c.has_key ? "已设置（留空则不修改）" : "在此输入 API 密钥";
    } catch {}
  }
  $("iface-modal").hidden = false;
}
function closeIfaceConfig() { $("iface-modal").hidden = true; }
async function saveIfaceConfig() {
  const name = $("if-name").value.trim();
  if (!name) return;
  await api("/api/interface/save", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, base_url: $("if-base").value.trim(), model: $("if-model").value.trim(),
      temperature: $("if-temp").value.trim(), top_p: $("if-topp").value.trim(), api_key: $("if-key").value,
      rpm: $("if-rpm").value.trim(), thread_count: $("if-thread").value.trim(),
      max_retries: $("if-retries").value.trim() }) });
  closeIfaceConfig(); loadInterfaces();
}
async function deleteIface() {
  if (!_ifaceEditing) return;
  await api("/api/interface/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: _ifaceEditing }) });
  closeIfaceConfig(); loadInterfaces();
}
if ($("add-interface")) $("add-interface").onclick = () => openIfaceConfig(null);
if ($("if-cancel")) $("if-cancel").onclick = closeIfaceConfig;
if ($("if-save")) $("if-save").onclick = saveIfaceConfig;
if ($("if-delete")) $("if-delete").onclick = deleteIface;
if ($("iface-modal")) $("iface-modal").onclick = (e) => { if (e.target.id === "iface-modal") closeIfaceConfig(); };

// ----- interface language (i18n) -----
const _UI_LANGS = [["en", "English"], ["zh", "简体中文"], ["zh-Hant", "繁體中文"], ["ja", "日本語"]];
let _uiLang = "zh";   // current UI language (for label_en vs label choice)
function applyI18n(lang) {
  _uiLang = lang || _uiLang;
  const L = (BOOT.labels && BOOT.labels[lang]) || {};
  const EN = (BOOT.labels && BOOT.labels.en) || {};
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.dataset.i18n;
    el.textContent = L[k] || EN[k] || el.textContent;
  });
  // Attribute-targeted i18n: placeholders + titles (used by the Quick page).
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    const k = el.dataset.i18nPh;
    el.placeholder = L[k] || EN[k] || el.placeholder;
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const k = el.dataset.i18nTitle;
    el.title = L[k] || EN[k] || el.title;
  });
  localStorage.setItem("lh-lang", lang);
}
function initUiLang() {
  const sel = $("ui-lang");
  if (!sel) return;
  sel.innerHTML = "";
  for (const [code, label] of _UI_LANGS) {
    const o = document.createElement("option"); o.value = code; o.textContent = label; sel.appendChild(o);
  }
  const saved = localStorage.getItem("lh-lang") || "zh";
  sel.value = saved;
  applyI18n(saved);
  sel.onchange = () => applyI18n(sel.value);
}

// ----- theme -----
function applyTheme(theme) {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme === "dark" ? "dark" : "light";
  $("theme-toggle").innerHTML = theme === "dark" ? ICON.sun : ICON.moon;
  const tm = $("theme-toggle-m"); if (tm) tm.innerHTML = theme === "dark" ? ICON.sun : ICON.moon;
  localStorage.setItem("lh-theme", theme);
  if (window.LHBackground) window.LHBackground.setMode(theme);
  // Some engines don't recompute var()-based inherited `color` on attribute
  // change when compositing layers (backdrop-filter) are present; force one
  // synchronous restyle so every already-rendered surface tracks the theme.
  if (document.body) {
    document.body.style.display = "none";
    void document.body.offsetHeight;
    document.body.style.display = "";
  }
}
$("theme-toggle").onclick = () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");

// ----- mobile drawer + mobile theme toggle -----
const _toggleTheme = () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
if ($("theme-toggle-m")) $("theme-toggle-m").onclick = _toggleTheme;
if ($("nav-toggle")) $("nav-toggle").onclick = () => document.body.classList.toggle("nav-open");
if ($("scrim")) $("scrim").onclick = () => document.body.classList.remove("nav-open");

// ----- translate sub-mode: document / live -----
document.querySelectorAll("#translate-mode .seg").forEach((s) => {
  s.onclick = () => {
    document.querySelectorAll("#translate-mode .seg").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll('.panel[data-panel="translate"] .subpane').forEach((x) => x.classList.remove("active"));
    s.classList.add("active");
    document.querySelector(`.panel[data-panel="translate"] .subpane[data-sub="${s.dataset.sub}"]`).classList.add("active");
  };
});

// ----- bootstrap -----
async function boot() {
  applyTheme(localStorage.getItem("lh-theme") || "light");
  BOOT = await api("/api/bootstrap");
  const c = BOOT.config;

  // Source supports auto-detection ("Auto"); target is always concrete.
  fillSelect($("src-lang"), ["Auto"].concat(BOOT.languages), c.default_src_lang || "Auto");
  fillSelect($("dst-lang"), BOOT.languages, c.default_dst_lang);
  fillSelect($("model"), modelsForMode(c.default_online), c.default_online_model);
  fillSelect($("glossary"), BOOT.glossaries, c.default_glossary);
  refreshSttPicker();   // only DOWNLOADED STT models are offered
  $("translate-subs").checked = c.translate_subtitles;

  // settings (per-model key/RPM/thread/retries now live in Interface Management)
  $("set-lan").checked = !!c.lan_mode;
  $("set-lan-admin").placeholder = c.has_lan_admin ? "已设置（留空则不修改）" : "留空则不启用";
  $("set-auto-glossary").checked = !!c.auto_extract_glossary;
  if ($("set-translation-mode")) {
    const sel = $("set-translation-mode");
    if (!sel.options.length) {
      for (const m of (BOOT.translation_modes || [])) {
        const o = document.createElement("option");
        o.value = m.id;
        // Chinese UI -> Chinese label; otherwise the English label (more
        // universal than Chinese for ja/fr/… UIs).
        o.textContent = _uiLang.startsWith("zh") ? (m.label || m.id) : (m.label_en || m.label || m.id);
        sel.appendChild(o);
      }
    }
    sel.value = c.translation_mode || "precise";
  }
  if ($("set-tone")) $("set-tone").value = c.translation_tone || "";
  if ($("set-length")) $("set-length").value = c.translation_length || "";
  if ($("set-style")) $("set-style").value = c.translation_style || "";
  if ($("set-mask-ph")) $("set-mask-ph").checked = c.mask_placeholders !== false;
  if ($("set-dedup-context")) $("set-dedup-context").checked = !!c.dedup_context;
  if ($("set-with-context")) $("set-with-context").checked = !!c.translate_with_context;
  if ($("set-image-lama")) $("set-image-lama").checked = !!c.image_inpaint_lama;
  if ($("set-translation-cache")) { $("set-translation-cache").checked = !!c.translation_cache; refreshCacheStats(); }
  if ($("set-bi-bold")) $("set-bi-bold").checked = c.bilingual_bold !== false;
  if ($("set-bi-color")) $("set-bi-color").value = c.bilingual_color || "";
  if ($("set-live-stream")) $("set-live-stream").checked = !!c.live_stream_translation;
  if ($("set-web-vad")) $("set-web-vad").value = c.web_vad || "energy";
  if ($("set-vad-hang")) $("set-vad-hang").value = String(c.live_vad_hang_ms || 900);
  if ($("set-vad-sens")) $("set-vad-sens").value = c.live_vad_sensitivity || "standard";
  if ($("set-vad-maxseg")) $("set-vad-maxseg").value = String(c.live_vad_max_seg_ms || 30000);
  if ($("set-result-dir")) $("set-result-dir").value = c.result_dir || "";
  if ($("set-hist-max")) $("set-hist-max").value = (c.history_max_records ?? 1000);
  if ($("set-hist-age")) $("set-hist-age").value = (c.history_max_age_days ?? 0);
  if ($("set-log-max")) $("set-log-max").value = (c.log_max_files ?? 500);
  if ($("set-log-age")) $("set-log-age").value = (c.log_max_age_days ?? 30);
  if ($("set-log-size")) $("set-log-size").value = (c.log_max_size_mb ?? 500);
  if ($("set-result-size")) $("set-result-size").value = (c.result_max_size_mb ?? 5000);
  // PDF options (Translate page; shown only when a PDF is selected)
  if ($("pdf-translate-table")) $("pdf-translate-table").checked = !!c.pdf_translate_table;
  if ($("pdf-ocr-scanned")) $("pdf-ocr-scanned").checked = !!c.pdf_ocr_scanned;
  if ($("pdf-dual-alternating")) $("pdf-dual-alternating").checked = !!c.pdf_dual_alternating;
  if ($("pdf-pages")) $("pdf-pages").value = c.pdf_pages || "";
  if ($("pdf-only-translated")) $("pdf-only-translated").checked = !!c.pdf_only_translated_pages;
  if ($("manga-mode")) $("manga-mode").checked = !!c.manga_mode;
  fillSelect($("glossary-edit-select"), BOOT.glossaries, c.default_glossary);
  initQuick();
  renderModules();
  fillLiveTarget();
  refreshLiveModel();
  updateLiveHint();
  if (BOOT.server_mode) applyServerMode();
  refreshApiKeyState();
  refreshModels();
  refreshMediaNote();
  renderDropBg();
  initUiLang();
  checkUpdate();
  maybeShowOnboarding();
}

// ----- first-run onboarding: interactive spotlight tour -----
// Each step navigates to a nav tab, spotlights the key control on that page,
// rings the active nav tab, and floats a tooltip beside it.
const _TOUR_STEPS = [
  { tab: "interface", sel: "#add-interface",          t: "Onboarding T1 Title", b: "Onboarding T1 Body" },
  { tab: "quick",     sel: "#quick-input",            t: "Onboarding T2 Title", b: "Onboarding T2 Body" },
  { tab: "translate", sel: "#dropzone",               t: "Onboarding T3 Title", b: "Onboarding T3 Body" },
  { tab: "live",      sel: "#live-go",                t: "Onboarding T4 Title", b: "Onboarding T4 Body" },
  { tab: "glossary",  sel: "#glossary-edit-select",   t: "Onboarding T5 Title", b: "Onboarding T5 Body" },
  { tab: "modules",   sel: "#modules-grid",           t: "Onboarding T6 Title", b: "Onboarding T6 Body" },
  { tab: "settings",  sel: "#set-translation-mode",   t: "Onboarding T7 Title", b: "Onboarding T7 Body" },
];
let _tourStep = 0;
function _olbl(key) {
  const L = (BOOT.labels && BOOT.labels[_uiLang]) || {};
  const EN = (BOOT.labels && BOOT.labels.en) || {};
  return L[key] || EN[key] || key;
}
function _tourClearNavHi() {
  document.querySelectorAll(".tab.tour-navhi").forEach((t) => t.classList.remove("tour-navhi"));
}
function _placeTour(rect) {
  const hole = $("tour-hole"), pop = $("tour-pop");
  const pad = 8;
  const top = Math.max(0, rect.top - pad), left = Math.max(0, rect.left - pad);
  const w = rect.width + pad * 2, h = rect.height + pad * 2;
  hole.style.top = top + "px"; hole.style.left = left + "px";
  hole.style.width = w + "px"; hole.style.height = h + "px";
  // Place the popup where there's room: right of target, else left, else below, else above.
  const pw = pop.offsetWidth || 330, ph = pop.offsetHeight || 160, gap = 16;
  const vw = window.innerWidth, vh = window.innerHeight;
  let px, py;
  if (rect.right + gap + pw <= vw) px = rect.right + gap;
  else if (rect.left - gap - pw >= 0) px = rect.left - gap - pw;
  else px = Math.min(Math.max(8, rect.left), vw - pw - 8);
  if (px === rect.right + gap || px === rect.left - gap - pw) {
    py = Math.min(Math.max(8, rect.top), vh - ph - 8);     // beside: align to target top
  } else if (rect.bottom + gap + ph <= vh) {
    py = rect.bottom + gap;                                 // below
  } else {
    py = Math.max(8, rect.top - gap - ph);                 // above
  }
  pop.style.left = px + "px"; pop.style.top = py + "px";
}
function _renderTour() {
  const s = _TOUR_STEPS[_tourStep];
  // Navigate to the page (also sets the nav tab .active).
  const tabBtn = document.querySelector(`.tab[data-tab="${s.tab}"]`);
  if (tabBtn) tabBtn.click();
  _tourClearNavHi();
  if (tabBtn) tabBtn.classList.add("tour-navhi");
  // Tooltip content.
  $("tour-pop-title").textContent = _olbl(s.t);
  $("tour-pop-body").textContent = _olbl(s.b);
  $("tour-skip").textContent = _olbl("Onboarding Skip");
  $("tour-back").textContent = _olbl("Onboarding Back");
  $("tour-back").hidden = _tourStep === 0;
  const last = _tourStep === _TOUR_STEPS.length - 1;
  $("tour-next").textContent = last ? _olbl("Onboarding Done") : _olbl("Onboarding Next");
  $("tour-dots").innerHTML = _TOUR_STEPS
    .map((_, i) => `<span class="${i === _tourStep ? "on" : ""}"></span>`).join("");
  // Let the panel render, then spotlight the target (fall back to the nav tab).
  requestAnimationFrame(() => setTimeout(() => {
    let el = document.querySelector(s.sel);
    if (!el || el.offsetParent === null) el = tabBtn;     // hidden/missing -> ring the tab itself
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "auto" });
    _placeTour(el.getBoundingClientRect());
  }, 60));
}
function _closeTour() {
  $("tour").hidden = true;
  _tourClearNavHi();
  window.removeEventListener("resize", _onTourResize);
  try { localStorage.setItem("lh-onboarded", "1"); } catch (e) {}
}
function _onTourResize() {
  const s = _TOUR_STEPS[_tourStep];
  let el = document.querySelector(s.sel) || document.querySelector(`.tab[data-tab="${s.tab}"]`);
  if (el) _placeTour(el.getBoundingClientRect());
}
function maybeShowOnboarding() {
  if (BOOT.server_mode) return;                 // public deploy: anonymous users, no setup
  let seen = false;
  try { seen = localStorage.getItem("lh-onboarded") === "1"; } catch (e) {}
  if (seen) return;
  _tourStep = 0;
  $("tour").hidden = false;
  window.addEventListener("resize", _onTourResize);
  _renderTour();
}
if ($("tour-skip")) $("tour-skip").onclick = _closeTour;
if ($("tour-back")) $("tour-back").onclick = () => { if (_tourStep > 0) { _tourStep--; _renderTour(); } };
if ($("tour-next")) $("tour-next").onclick = () => {
  if (_tourStep < _TOUR_STEPS.length - 1) { _tourStep++; _renderTour(); }
  else _closeTour();
};

// ----- update banner -----
async function checkUpdate() {
  try {
    const u = await api("/api/update-check");
    if (u && u.update) {
      $("update-text").textContent = `${_label("Update Available", "发现新版本")} ${u.latest}` +
        `（${_label("Current Version", "当前")} ${u.current}）`;
      $("update-link").href = u.url;
      // Portable build with a direct package URL -> offer one-click in-app update
      // (keeps installed plugins + models). Otherwise just the download link.
      if ($("update-now")) $("update-now").hidden = !u.asset_url;
      $("update-banner").hidden = false;
    }
  } catch (e) { /* offline / unreachable — silently skip */ }
}
$("update-dismiss").onclick = () => { $("update-banner").hidden = true; };
if ($("update-now")) $("update-now").onclick = async () => {
  const btn = $("update-now");
  btn.disabled = true;
  try {
    await api("/api/self-update", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  } catch (e) {
    btn.disabled = false;
    $("update-text").textContent = (e && e.message) || _label("Try Again Later", "请稍后重试");
    return;
  }
  const poll = setInterval(async () => {
    let s; try { s = await api("/api/self-update/status"); } catch (e) { return; }
    const pct = Math.round((s.progress || 0) * 100);
    $("update-text").textContent = `${_label("Updating", "正在更新")} ${pct}% (${s.stage || ""})`;
    if (s.status === "running") return;
    clearInterval(poll);
    btn.disabled = false;
    $("update-text").textContent = s.status === "done"
      ? `${_label("Update Done Restart", "更新完成，请重启程序")}`
      : `${_label("Update Failed", "更新失败")}: ${(s.message || "").slice(-200)}`;
    if (s.status === "done") btn.hidden = true;
  }, 1000);
};

// In public-deploy (server) mode the server owns the model + key, so hide the
// admin/settings UI and the per-translate model picker. Inline display:none
// beats the .tab/.field stylesheet rules (HTML [hidden] would be overridden).
function applyServerMode() {
  document.body.classList.add("server-mode");
  for (const t of ["settings", "history", "modules", "interface"]) {
    const btn = document.querySelector(`.tab[data-tab="${t}"]`);
    if (btn) btn.style.display = "none";
  }
  const modelField = $("model").closest(".field");
  if (modelField) modelField.style.display = "none";
  $("apikey-warning").hidden = true;
}

// File-type icons that drift across the drop-zone background (same SVG set as
// the Qt app, served from /assets/icons/filetypes/). [svgKey, suffix].
const _DROP_ICONS = [
  ["pdf", ".pdf"], ["docx", ".docx"], ["pptx", ".pptx"], ["xlsx", ".xlsx"],
  ["epub", ".epub"], ["txt", ".txt"], ["md", ".md"], ["srt", ".srt"],
  ["srt", ".vtt"], ["csv", ".csv"], ["json", ".json"], ["html", ".html"],
  ["img", ".png"], ["img", ".jpg"], ["media", ".mp4"], ["media", ".mp3"],
];

// Fill the drop zone background with two rows of slowly scrolling file-type
// icons (CSS animates them). Each row's set is duplicated so the -50% loop is
// seamless.
function renderDropBg() {
  const bg = $("drop-bg");
  if (!bg) return;
  bg.innerHTML = "";
  for (let row = 0; row < 2; row++) {
    const track = document.createElement("div");
    track.className = "ft-track ft-row-" + row;
    for (let dup = 0; dup < 2; dup++) {
      for (const [key, suf] of _DROP_ICONS) {
        const sp = document.createElement("span");
        sp.className = "ft";
        sp.innerHTML = `<img src="/assets/icons/filetypes/${key}.svg" alt=""><i>${suf}</i>`;
        track.appendChild(sp);
      }
    }
    bg.appendChild(track);
  }
}

// ----- API key state -----
async function refreshApiKeyState() {
  if (BOOT.server_mode) { $("apikey-warning").hidden = true; return; }
  const online = useOnline();
  const model = $("model").value;
  if (!online) { $("apikey-warning").hidden = true; return; }
  const st = await api("/api/apikey?model=" + encodeURIComponent(model));
  $("apikey-warning").hidden = st.has_key;
}


// ----- model/lang/online wiring -----
$("model").onchange = refreshApiKeyState;
$("src-lang").onchange = () => saveConfig({ default_src_lang: $("src-lang").value });
$("dst-lang").onchange = () => saveConfig({ default_dst_lang: $("dst-lang").value });
$("glossary").onchange = () => saveConfig({ default_glossary: $("glossary").value });
$("swap").onclick = () => {
  const s = $("src-lang").value; $("src-lang").value = $("dst-lang").value; $("dst-lang").value = s;
  saveConfig({ default_src_lang: $("src-lang").value, default_dst_lang: $("dst-lang").value });
};

$("stt-model").onchange = () => {
  saveConfig({ stt_model: $("stt-model").value });
  applySenseVoiceRestriction();
  refreshMediaNote();
};
$("translate-subs").onchange = () => saveConfig({ translate_subtitles: $("translate-subs").checked });

function isSenseVoice(sttId) {
  return (sttId || "").startsWith("sensevoice");
}
function applySenseVoiceRestriction() {
  const sttId = $("stt-model").value;
  const cur = $("src-lang").value;
  let langs = BOOT.languages;
  if (isSenseVoice(sttId)) {
    const codes = new Set(BOOT.sensevoice_codes);
    langs = BOOT.languages.filter((n) => codes.has(BOOT.language_map[n]));
  }
  langs = ["Auto"].concat(langs);   // source auto-detect always available
  fillSelect($("src-lang"), langs, langs.includes(cur) ? cur : "Auto");
}
function refreshMediaNote() {
  $("media-note").textContent = isSenseVoice($("stt-model").value)
    ? "SenseVoice 仅支持 中/繁/英/日/韩，源语言已自动限制。" : "";
}

async function saveConfig(obj) {
  // The server config is admin-owned in server mode; never persist per-user prefs.
  if (BOOT && BOOT.server_mode) return;
  try { await api("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) }); }
  catch (e) { console.warn("saveConfig", e); }
}

// ----- file upload (drag/drop + click) -----
const dz = $("dropzone");
dz.onclick = () => $("file-input").click();
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
dz.ondragleave = () => dz.classList.remove("dragover");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("dragover"); if (e.dataTransfer.files.length) setFiles([...e.dataTransfer.files]); };
$("file-input").onchange = (e) => { if (e.target.files.length) setFiles([...e.target.files]); };

function setFiles(list) {
  currentFiles = list;
  if (list.length === 1) {
    $("drop-text").textContent = list[0].name + "  (" + (list[0].size / 1048576).toFixed(1) + " MB)";
  } else {
    $("drop-text").textContent = `${list.length} 个文件：` + list.map((f) => f.name).join("、").slice(0, 80);
  }
  const anyMedia = list.some((f) => MEDIA_EXTS.includes("." + f.name.split(".").pop().toLowerCase()));
  $("media-options").hidden = !anyMedia;
  if (anyMedia) { refreshSttPicker().then(applySenseVoiceRestriction); }
  const anyPdf = list.some((f) => f.name.split(".").pop().toLowerCase() === "pdf");
  $("pdf-options").hidden = !anyPdf;
  // 漫画模式 applies to PDFs and images (bubble-group + vertical typeset).
  const anyImage = list.some((f) => IMAGE_EXTS.includes("." + f.name.split(".").pop().toLowerCase()));
  if ($("manga-options")) $("manga-options").hidden = !(anyPdf || anyImage);
  // PDF-specific guidance (full scans -> manga mode; PDF-Options OCR is only for
  // mostly-digital PDFs) only makes sense when a PDF is selected.
  if ($("manga-pdf-hint")) $("manga-pdf-hint").hidden = !anyPdf;
}

// ----- translate -----
// Return true if every uploaded file's format has its required plugin installed.
// Otherwise warn (per missing plugin) and offer to jump to the Plugins page.
function requiredPluginsReady(files) {
  const extMap = (BOOT && BOOT.ext_plugin) || {};
  const avail = {};
  for (const m of (BOOT.modules || [])) avail[m.name] = m.available;
  const mangaOn = !!(BOOT.config && BOOT.config.manga_mode);
  const needed = new Set();
  for (const f of files) {
    const ext = "." + (f.name.split(".").pop() || "").toLowerCase();
    // 漫画模式: a PDF/image is translated via the image pipeline (OCR), so it needs
    // the Image OCR plugin regardless of its normal plugin (PDF -> BabelDOC).
    let plugin = extMap[ext];
    if (mangaOn && (ext === ".pdf" || IMAGE_EXTS.includes(ext))) plugin = "Image OCR";
    if (plugin && avail[plugin] === false) needed.add(plugin);
  }
  if (!needed.size) return true;
  const names = [...needed].join("、");
  const msg = `${names} ${_label("Plugin Needed For File", "插件未安装,无法翻译该文件。是否前往「插件」页安装？")}`;
  if (confirm(msg)) {
    const t = document.querySelector('.tab[data-tab="modules"]'); if (t) t.click();
  }
  return false;
}

$("translate-btn").onclick = async () => {
  if (!currentFiles.length) { setStatus("请先选择文件。"); return; }
  // Pre-check: a file whose format needs an OPTIONAL plugin that isn't installed
  // would just fail mid-run. Warn up front and offer to go install it.
  if (!requiredPluginsReady(currentFiles)) return;
  const online = useOnline();
  if (online && !BOOT.server_mode) {
    if (!($("model").value || "").trim()) {   // parity with Qt: require a model
      setStatus(_label("Please select a model first", "请先选择一个模型（接口管理）。")); return;
    }
    const st = await api("/api/apikey?model=" + encodeURIComponent($("model").value));
    if (!st.has_key) { setStatus(_apiKeyMissingMsg($("model").value)); return; }
  }
  setRunState("running");   // hide the form, show the dashboard (Qt-style takeover)
  $("result").hidden = true; setStatus("");

  // For each video, extract the audio track in-browser to avoid uploading the
  // whole file (the result is only a subtitle file anyway). Client-side events
  // (extraction start/progress/fail) are collected and sent so they land in the
  // project log from the moment "开始翻译" is clicked.
  const fd = new FormData();
  const clientLog = [];
  const ts = () => new Date().toTimeString().slice(0, 8);
  for (const f of currentFiles) {
    let uploadFile = f;
    const ext = "." + f.name.split(".").pop().toLowerCase();
    if (VIDEO_EXTS.includes(ext)) {
      const sizeMB = (f.size / 1048576).toFixed(1);
      setStatus(`正在浏览器内提取音轨：${f.name}（避免上传整段视频）…`);
      setExtractProgress(0, `提取音轨：${f.name}…`);
      clientLog.push(`${ts()} audio-extract start: ${f.name} (${sizeMB} MB)`);
      const t0 = performance.now();
      try {
        uploadFile = await extractAudio(f, (p) =>
          setExtractProgress(p, `提取音轨：${f.name} ${Math.round(p * 100)}%`));
        const secs = ((performance.now() - t0) / 1000).toFixed(1);
        setExtractProgress(1, `音轨提取完成：${uploadFile.name}`);
        clientLog.push(`${ts()} audio-extract ok: ${f.name} -> ${uploadFile.name} `
          + `(${(uploadFile.size / 1048576).toFixed(1)} MB, ${secs}s)`);
      }
      catch (e) {
        console.warn("ffmpeg.wasm failed, uploading original:", e);
        uploadFile = f;
        // Extraction failed (often the ffmpeg.wasm CDN is blocked, or the video
        // is too big for browser memory) — we now upload the whole video and let
        // the server extract audio. Say so: a multi-GB upload is slower.
        setStatus(`音轨提取未成功，将上传整段视频（${f.name}，可能较慢）…`);
        clientLog.push(`${ts()} audio-extract FAILED: ${f.name} — `
          + `${(e && e.message) || e}; uploading full video instead`);
      }
    }
    fd.append("files", uploadFile);
  }
  fd.append("src_lang", $("src-lang").value);
  fd.append("dst_lang", $("dst-lang").value);
  fd.append("model", $("model").value);
  fd.append("use_online", online);
  fd.append("glossary", $("glossary").value);
  fd.append("bilingual", $("translate-bilingual").checked);
  fd.append("ui_lang", _uiLang);
  if (clientLog.length) fd.append("client_log", JSON.stringify(clientLog));

  try {
    const { task_id } = await api("/api/translate", { method: "POST", body: fd });
    currentTask = task_id;
    listenProgress(task_id);
  } catch (e) { setRunState("idle"); setStatus("错误: " + e.message); }
};

// ----- run controls (pause / resume / stop / back) -----
$("run-pause").onclick = async () => {
  if (!currentTask) return;
  setRunState("paused"); setStatus(_label("Paused", "已暂停")); pauseElapsed();   // optimistic; SSE confirms
  try { await api("/api/pause/" + currentTask, { method: "POST" }); } catch (e) {}
};
$("run-resume").onclick = async () => {
  if (!currentTask) return;
  setRunState("running"); setStatus(""); resumeElapsed();
  try { await api("/api/resume/" + currentTask, { method: "POST" }); } catch (e) {}
};
$("run-stop").onclick = async () => {
  if (!currentTask) return;
  setStatus(_label("Stopping", "正在停止") + "…");
  try { await api("/api/stop/" + currentTask, { method: "POST" }); } catch (e) {}
};
$("run-back").onclick = async () => {
  // From a PAUSED run, "返回" also stops the task (it's saved to history and can
  // be continued later); from a finished run it's just "new translation".
  if (_runState === "paused" && currentTask) {
    try { await api("/api/stop/" + currentTask, { method: "POST" }); } catch (e) {}
  }
  if (_progressES) { try { _progressES.close(); } catch (e) {} }
  stopElapsed();
  stopSysmonPoll();
  currentTask = null;
  setRunState("idle");
};

let _progressES = null;
function listenProgress(taskId) {
  // Reset the dashboard so a previous run's numbers don't linger on screen.
  ["m-files", "m-speed", "m-tokens", "m-eta", "m-threads", "m-cpu", "m-gpu"].forEach((id) => { const e = $(id); if (e) e.textContent = "—"; });
  if ($("m-cost")) $("m-cost").textContent = "";
  // Compute device (static, from bootstrap): tells the user GPU vs CPU.
  const _hw = BOOT.hardware || {};
  if ($("m-device")) $("m-device").textContent =
    (_hw.gpu ? _label("GPU", "GPU") : _label("CPU", "CPU")) + (_hw.name ? " · " + _hw.name.slice(0, 16) : "");
  startSysmonPoll();
  $("progress-bar").style.width = "0%";
  $("prog-ring").style.setProperty("--p", "0deg");
  $("prog-pct").textContent = "0%";
  $("progress-desc").textContent = "";
  startElapsed();
  $("translate-run").scrollIntoView({ behavior: "smooth", block: "start" });
  if (_progressES) { try { _progressES.close(); } catch (e) {} }   // don't leak a prior stream
  const es = new EventSource("/api/progress/" + taskId);
  _progressES = es;
  es.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    const pct = Math.round((d.progress || 0) * 100);
    $("progress-bar").style.width = pct + "%";
    $("prog-ring").style.setProperty("--p", (pct * 3.6) + "deg");
    $("prog-pct").textContent = pct + "%";
    $("progress-desc").textContent = d.desc || "";
    // Parse the backend stats line into the dashboard metric cards.
    const desc = d.desc || "";
    const m = (re) => { const x = desc.match(re); return x ? x[1] : "—"; };
    // File progress: "[2/3] name: ..." while running, "(2/3)" on the done line.
    const fm = desc.match(/^\[(\d+)\/(\d+)\]/) || desc.match(/\((\d+)\/(\d+)\)/);
    if (fm) $("m-files").textContent = fm[1] + "/" + fm[2];
    $("m-speed").textContent = m(/([\d.]+)\s*lines\/min/i);
    // Tokens: prefer the authoritative live count from the payload (also drives
    // the live cost); fall back to parsing the desc.
    if (d.tokens != null) $("m-tokens").textContent = _fmtTokens(d.tokens);
    else $("m-tokens").textContent = (desc.match(/([\d.]+\s*[KMkm]?)\s*tokens/i) || [, "—"])[1].replace(/\s/g, "");
    // Live cost estimate (parity with Qt) — shown whenever the payload carries it.
    if ($("m-cost")) $("m-cost").textContent = d.cost ? ("≈" + (d.cost.symbol || "") + d.cost.amount) : "";
    $("m-eta").textContent = m(/ETA\s+([\d:]+)/i);
    $("m-threads").textContent = m(/(\d+)\s*threads/i);
    // Sync the pause UI to the server's authoritative state.
    if (d.status === "running") {
      if (d.paused && _runState !== "paused") { setRunState("paused"); setStatus(_label("Paused", "已暂停")); pauseElapsed(); }
      else if (!d.paused && _runState === "paused") { setRunState("running"); setStatus(""); resumeElapsed(); }
    }
    if (d.status === "done") {
      es.close(); stopElapsed(); stopSysmonPoll(); setRunState("done");
      $("download-link").href = "/api/download/" + taskId;
      $("result").hidden = false; setStatus("翻译完成");
      if ($("m-cost") && d.cost) $("m-cost").textContent = "≈" + (d.cost.symbol || "") + d.cost.amount;
      renderCoverage(d.coverage);
      renderQa(d.qa);
      showThanks(d.tokens, d.cost);
    } else if (d.status === "error") {
      es.close(); stopElapsed(); stopSysmonPoll(); setRunState("error"); setStatus("错误: " + (d.error || "未知错误"));
    } else if (d.status === "stopped") {
      es.close(); stopElapsed(); stopSysmonPoll(); setRunState("stopped"); setStatus(_label("Stopped", "已停止"));
    }
  };
  es.onerror = () => { es.close(); };
}

// Poll live CPU/GPU usage while a run is active (the compute-device card is
// static, set from BOOT.hardware). Best-effort; "—" when unavailable.
let _sysmonTimer = null;
function startSysmonPoll() {
  stopSysmonPoll();
  const tick = async () => {
    try {
      const u = await api("/api/sysmon");
      if ($("m-cpu")) $("m-cpu").textContent = (u.cpu == null) ? "—" : Math.round(u.cpu) + "%";
      if ($("m-gpu")) {
        const mem = (u.gpu_mem_total) ? ` (${u.gpu_mem_used}/${u.gpu_mem_total}MB)` : "";
        $("m-gpu").textContent = (u.gpu == null) ? "—" : Math.round(u.gpu) + "%" + mem;
      }
    } catch (e) { /* transient */ }
  };
  tick();
  _sysmonTimer = setInterval(tick, 2000);
}
function stopSysmonPoll() { if (_sysmonTimer) { clearInterval(_sysmonTimer); _sysmonTimer = null; } }

function _fmtTokens(n) {
  n = +n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1000) return (n / 1000).toFixed(n >= 1e4 ? 0 : 1) + "K";
  return String(Math.round(n));
}

function _label(key, fallback) {
  const lang = localStorage.getItem("lh-lang") || "zh";
  const L = (BOOT.labels && BOOT.labels[lang]) || {};
  const EN = (BOOT.labels && BOOT.labels.en) || {};
  return L[key] || EN[key] || fallback;
}

// Specific "this model has no key" message: names the SELECTED model and points to
// 接口管理 (where keys live now — NOT 设置), so a user who set one provider's key
// but has a DIFFERENT, keyless model selected (e.g. a local interface) understands
// it's the selected model that needs a key, not a global setting.
function _apiKeyMissingMsg(model) {
  const lang = localStorage.getItem("lh-lang") || "zh";
  const m = model || "";
  if (lang.startsWith("zh")) {
    return `当前模型「${m}」尚未配置 API 密钥。请到「接口管理」双击该接口填写密钥，或改用已配置密钥的接口。`;
  }
  return `The selected model "${m}" has no API key. Open Interface Management, double-click it to set its key, or switch to an interface that already has one.`;
}

// Compact coverage panel: "正文 80 · 表格 20 · 批注 10 · 0 未翻译".
function renderCoverage(cov) {
  const box = $("coverage");
  if (!box) return;
  if (!cov || !cov.total) { box.hidden = true; return; }
  const parts = [];
  for (const [cat, n] of Object.entries(cov.by_category || {})) {
    if (n) parts.push(`${cat} ${n}`);
  }
  parts.push(`${cov.fallback || 0} ${_label("Untranslated", "未翻译")}`);
  if (cov.needs_review) parts.push(`${cov.needs_review} ${_label("Needs review", "需复核")}`);
  $("coverage-body").textContent = parts.join(" · ");
  box.hidden = false;
}

// Friendly label per QA check key.
const _QA_LABELS = {
  placeholders: ["Placeholder mismatch", "占位符不一致"],
  length_ratio: ["Length anomaly", "长度异常"],
  subtitle_length: ["Subtitle line too wide", "字幕行过宽"],
  subtitle_lines: ["Subtitle >2 lines", "字幕超过2行"],
  subtitle_cps: ["Reading speed too fast", "阅读速度过快"],
  glossary_terms: ["Glossary term not applied", "术语未应用"],
};

// Collapsible quality-warning panel: each failed check + count, expandable to
// the offending segment ids / glossary terms. Hidden when there are no warnings.
function renderQa(qa) {
  const box = $("qa-panel");
  if (!box) return;
  const keys = qa ? Object.keys(qa).filter((k) => (qa[k] || []).length) : [];
  if (!keys.length) { box.hidden = true; return; }
  const total = keys.reduce((n, k) => n + qa[k].length, 0);
  const head = $("qa-head");
  head.textContent = `${_label("Quality warnings", "质量提示")} (${total})`;
  const body = $("qa-body");
  body.replaceChildren();
  for (const k of keys) {
    const items = qa[k];
    const row = document.createElement("div"); row.className = "qa-row";
    const lbl = _label(_QA_LABELS[k]?.[0] || k, _QA_LABELS[k]?.[1] || k);
    let detail;
    if (k === "glossary_terms") {   // [{id, term, expected}] — user glossary content
      detail = items.slice(0, 12).map((it) => `#${it.id} ${it.term}→${it.expected}`).join("、");
    } else {                         // [count_src ...]
      detail = items.slice(0, 30).map((id) => `#${id}`).join(" ");
    }
    if (items.length > (k === "glossary_terms" ? 12 : 30)) detail += " …";
    // Build with DOM APIs + textContent (detail can contain user glossary terms /
    // source text — never inject as HTML).
    const kSpan = document.createElement("span"); kSpan.className = "qa-k";
    kSpan.append(lbl + " ");
    const b = document.createElement("b"); b.textContent = items.length; kSpan.append(b);
    const dSpan = document.createElement("span"); dSpan.className = "qa-d";
    dSpan.textContent = detail;
    row.append(kSpan, dSpan);
    body.appendChild(row);
  }
  box.hidden = false;
}

// Run state machine driving the form<->dashboard takeover and the run buttons.
//   idle     -> form shown
//   running  -> dashboard; [暂停][停止]
//   paused   -> dashboard; [继续][返回]
//   done/stopped/error -> dashboard + result; [返回/新翻译]
let _runState = "idle";
function setRunState(state) {
  _runState = state;
  const idle = state === "idle";
  const tf = $("translate-form"), trun = $("translate-run");
  if (tf) tf.hidden = !idle;
  if (trun) trun.hidden = idle;
  const show = (id, on) => { const el = $(id); if (el) el.hidden = !on; };
  show("run-pause", state === "running");
  show("run-stop", state === "running");
  show("run-resume", state === "paused");
  show("run-back", state === "paused" || state === "done" || state === "stopped" || state === "error");
  const backSpan = $("run-back") && $("run-back").querySelector("span");
  if (backSpan) backSpan.textContent = (state === "paused")
    ? _label("Back", "返回") : _label("New Translation", "新翻译");
  if (idle) { if ($("result")) $("result").hidden = true; setStatus(""); }
}
function setStatus(t) { $("status").textContent = t; }

// Elapsed-time clock: ticks every second so the dashboard shows progress even
// during opaque phases (audio extraction / STT) that emit no progress events —
// matches the Qt "Elapsed Time" card. Excludes paused time.
let _elapTimer = null, _elapBase = 0, _elapSegStart = 0, _elapPaused = false;
function _fmtDur(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(x)}` : `${p(m)}:${p(x)}`;
}
function _elapNow() { return _elapBase + (_elapPaused ? 0 : (Date.now() - _elapSegStart) / 1000); }
function startElapsed() {
  _elapBase = 0; _elapSegStart = Date.now(); _elapPaused = false;
  if ($("m-elapsed")) $("m-elapsed").textContent = "00:00";
  if (_elapTimer) clearInterval(_elapTimer);
  _elapTimer = setInterval(() => { if ($("m-elapsed")) $("m-elapsed").textContent = _fmtDur(_elapNow()); }, 1000);
}
function pauseElapsed() { if (_elapPaused) return; _elapBase += (Date.now() - _elapSegStart) / 1000; _elapPaused = true; }
function resumeElapsed() { if (!_elapPaused) return; _elapSegStart = Date.now(); _elapPaused = false; }
function stopElapsed() { if (_elapTimer) { clearInterval(_elapTimer); _elapTimer = null; } _elapPaused = false; }

// ----- settings -----
// (Online/offline is driven by the active interface, not a checkbox.)
$("set-lan").onchange = () => {
  saveConfig({ lan_mode: $("set-lan").checked });
  $("settings-status").textContent = "局域网模式已更新 —— 重启程序后生效。";
};
$("set-lan-admin").onchange = () => {
  const v = $("set-lan-admin").value;
  if (!v) return;                          // empty = keep the existing password
  saveConfig({ lan_admin_password: v });
  _adminToken = v;                          // in-memory, so the owner keeps access
  $("set-lan-admin").value = "";
  $("set-lan-admin").placeholder = "已设置（留空则不修改）";
  $("settings-status").textContent = "局域网管理密码已更新。";
};
$("set-auto-glossary").onchange = () => saveConfig({ auto_extract_glossary: $("set-auto-glossary").checked });
if ($("set-translation-mode")) $("set-translation-mode").onchange = () => saveConfig({ translation_mode: $("set-translation-mode").value });
if ($("set-tone")) $("set-tone").onchange = () => saveConfig({ translation_tone: $("set-tone").value });
if ($("set-length")) $("set-length").onchange = () => saveConfig({ translation_length: $("set-length").value });
if ($("set-style")) $("set-style").onchange = () => saveConfig({ translation_style: $("set-style").value.trim() });
if ($("set-mask-ph")) $("set-mask-ph").onchange = () => saveConfig({ mask_placeholders: $("set-mask-ph").checked });
if ($("set-dedup-context")) $("set-dedup-context").onchange = () => saveConfig({ dedup_context: $("set-dedup-context").checked });
if ($("set-with-context")) $("set-with-context").onchange = () => saveConfig({ translate_with_context: $("set-with-context").checked });
async function refreshCacheStats() {
  const el = $("cache-stats"); if (!el) return;
  try { const s = await api("/api/cache/stats"); el.textContent = `${s.rows} ${_label("entries", "条")} · ${(s.bytes / 1e6).toFixed(1)} MB`; }
  catch (e) { el.textContent = ""; }
}
if ($("set-translation-cache")) $("set-translation-cache").onchange = () => saveConfig({ translation_cache: $("set-translation-cache").checked });
if ($("cache-clear")) $("cache-clear").onclick = async () => {
  try { await api("/api/cache/clear", { method: "POST" }); toast(_label("Cache cleared", "缓存已清空"), "ok"); refreshCacheStats(); }
  catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
};
if ($("set-image-lama")) $("set-image-lama").onchange = async () => {
  const on = $("set-image-lama").checked;
  saveConfig({ image_inpaint_lama: on });
  if (on) {   // enabling -> fetch the model once (background); toast progress
    toast(_label("Downloading", "下载中…") + " LaMa…", "ok");
    try { await api("/api/inpaint-download", { method: "POST" }); toast(_label("Model Installed", "模型已安装") + " LaMa", "ok"); }
    catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
  }
};
if ($("set-bi-bold")) $("set-bi-bold").onchange = () => saveConfig({ bilingual_bold: $("set-bi-bold").checked });
if ($("set-bi-color")) $("set-bi-color").onchange = () => saveConfig({ bilingual_color: $("set-bi-color").value });
if ($("set-live-stream")) $("set-live-stream").onchange = () => {
  const v = $("set-live-stream").checked;
  if (BOOT.config) BOOT.config.live_stream_translation = v;   // applies without reload
  saveConfig({ live_stream_translation: v });
};
if ($("set-web-vad")) $("set-web-vad").onchange = () => {
  const v = $("set-web-vad").value;
  if (BOOT.config) BOOT.config.web_vad = v;   // applies on next live start
  saveConfig({ web_vad: v });
};
if ($("set-vad-hang")) $("set-vad-hang").onchange = () => {
  const v = parseInt($("set-vad-hang").value, 10) || 900;
  if (BOOT.config) BOOT.config.live_vad_hang_ms = v;   // applies on next live start
  saveConfig({ live_vad_hang_ms: v });
};
if ($("set-vad-sens")) $("set-vad-sens").onchange = () => {
  const v = $("set-vad-sens").value;
  if (BOOT.config) BOOT.config.live_vad_sensitivity = v;
  saveConfig({ live_vad_sensitivity: v });
};
if ($("set-vad-maxseg")) $("set-vad-maxseg").onchange = () => {
  const v = parseInt($("set-vad-maxseg").value, 10) || 30000;
  if (BOOT.config) BOOT.config.live_vad_max_seg_ms = v;
  saveConfig({ live_vad_max_seg_ms: v });
};
if ($("set-result-dir")) $("set-result-dir").onchange = () => saveConfig({ result_dir: $("set-result-dir").value.trim() || "data/result" });
if ($("set-result-browse")) $("set-result-browse").onclick = async () => {
  $("settings-status").textContent = "正在打开文件夹选择器…（窗口可能在后台）";
  try {
    const r = await api("/api/pick-folder", { method: "POST" });
    if (r && r.path) {
      $("set-result-dir").value = r.path;
      saveConfig({ result_dir: r.path });
      $("settings-status").textContent = "结果保存位置已更新。";
    } else { $("settings-status").textContent = ""; }
  } catch (e) { $("settings-status").textContent = "无法打开文件夹选择器：" + e.message; }
};
if ($("set-hist-max")) $("set-hist-max").onchange = () => saveConfig({ history_max_records: Math.max(0, parseInt($("set-hist-max").value || "0", 10) || 0) });
if ($("set-hist-age")) $("set-hist-age").onchange = () => saveConfig({ history_max_age_days: Math.max(0, parseInt($("set-hist-age").value || "0", 10) || 0) });
if ($("set-log-max")) $("set-log-max").onchange = () => saveConfig({ log_max_files: Math.max(0, parseInt($("set-log-max").value || "0", 10) || 0) });
if ($("set-log-age")) $("set-log-age").onchange = () => saveConfig({ log_max_age_days: Math.max(0, parseInt($("set-log-age").value || "0", 10) || 0) });
if ($("set-log-size")) $("set-log-size").onchange = () => saveConfig({ log_max_size_mb: Math.max(0, parseInt($("set-log-size").value || "0", 10) || 0) });
if ($("set-result-size")) $("set-result-size").onchange = () => saveConfig({ result_max_size_mb: Math.max(0, parseInt($("set-result-size").value || "0", 10) || 0) });
if ($("set-hist-clear")) $("set-hist-clear").onclick = async () => {
  if (!confirm("确定清空全部历史记录？此操作不可撤销。")) return;
  try {
    await api("/api/history/clear", { method: "POST" });
    $("settings-status").textContent = "历史记录已清空。";
    if (typeof loadHistory === "function") loadHistory();
  } catch (e) { $("settings-status").textContent = "清空失败：" + e.message; }
};
if ($("set-hist-clear-files")) $("set-hist-clear-files").onclick = async () => {
  if (!confirm("确定清空历史记录，并删除这些记录生成的译文/日志文件？\n你的原始文件不会被删除。此操作不可撤销。")) return;
  try {
    const d = await api("/api/history/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ with_files: true }) });
    $("settings-status").textContent = `历史记录已清空，删除了 ${d.files_deleted || 0} 个文件。`;
    if (typeof loadHistory === "function") loadHistory();
  } catch (e) { $("settings-status").textContent = "清空失败：" + e.message; }
};
// PDF options (Translate page) — persisted to config; backend reads them.
if ($("pdf-translate-table")) $("pdf-translate-table").onchange = () => saveConfig({ pdf_translate_table: $("pdf-translate-table").checked });
if ($("pdf-ocr-scanned")) $("pdf-ocr-scanned").onchange = () => saveConfig({ pdf_ocr_scanned: $("pdf-ocr-scanned").checked });
if ($("pdf-dual-alternating")) $("pdf-dual-alternating").onchange = () => saveConfig({ pdf_dual_alternating: $("pdf-dual-alternating").checked });
if ($("manga-mode")) $("manga-mode").onchange = () => {
  const on = $("manga-mode").checked;
  if (BOOT.config) BOOT.config.manga_mode = on;   // keep gating in sync without reload
  saveConfig({ manga_mode: on });
};
if ($("pdf-pages")) $("pdf-pages").onchange = () => saveConfig({ pdf_pages: $("pdf-pages").value.trim() });
if ($("pdf-only-translated")) $("pdf-only-translated").onchange = () => saveConfig({ pdf_only_translated_pages: $("pdf-only-translated").checked });
// Per-model key/RPM/thread/retries moved to Interface Management; their old
// Settings controls were removed.

// Trim the backend's verbose engine "detail" down to a short subtitle: keep
// only the engine name (the first "·"-separated segment), drop the long tail
// like "ffmpeg 已内置 · 视频字幕".
function _engineSubtitle(detail) {
  return (detail || "").split("·")[0].trim();
}

// Label shown for a plugin's currently selected model (falls back to the id).
function _currentModelLabel(m) {
  if (!m.models || !m.current_model) return "";
  const cur = m.models.find((x) => x.id === m.current_model);
  const full = cur ? cur.label : m.current_model;
  // Compact chip: drop the "(...)" detail; full label shows in the picker modal.
  return full.replace(/\s*[（(].*$/, "").trim() || full;
}

function renderModules() {
  const grid = $("modules-grid");
  grid.innerHTML = "";
  for (const m of BOOT.modules) {
    const card = document.createElement("div");
    card.className = "plugin-card" + (m.available ? " installed" : "");

    // Head: icon + name + status badge.
    const head = document.createElement("div"); head.className = "plugin-card-head";
    const ic = document.createElement("span"); ic.className = "plugin-card-icon";
    ic.innerHTML = PLUGIN_ICON[m.name] || ICON.download;
    const nm = document.createElement("div"); nm.className = "plugin-card-name"; nm.textContent = m.name;
    const statEl = document.createElement("span"); statEl.className = "plugin-card-status";
    statEl.innerHTML = m.available ? pill("on", _label("Installed", "已安装"), ICON.check)
                                   : pill("off", _label("Not Installed", "未安装"), ICON.cross);
    head.append(ic, nm, statEl);
    card.appendChild(head);

    // Engine subtitle (muted).
    const sub = document.createElement("div"); sub.className = "plugin-card-sub";
    sub.textContent = _engineSubtitle(m.detail);
    card.appendChild(sub);

    // Model line: clickable chip (selectable) / read-only fixed model / nothing.
    const modelLine = document.createElement("div"); modelLine.className = "plugin-card-model";
    if (m.models && m.models.length) {
      const chip = document.createElement("button");
      chip.type = "button"; chip.className = "model-chip";
      const txt = document.createElement("span"); txt.textContent = _currentModelLabel(m);
      const aff = document.createElement("span"); aff.className = "model-chip-aff"; aff.innerHTML = ICON.chevron;
      chip.append(txt, aff);
      chip.onclick = () => openPluginModelModal(m, chip, txt);
      modelLine.appendChild(chip);
    } else if (m.fixed_model) {
      modelLine.className = "plugin-card-model plugin-sub";
      modelLine.textContent = `${_label("Model", "模型")}: ${m.fixed_model}`;
    }
    card.appendChild(modelLine);

    // Usage line (downloaded models + disk), filled by loadModuleUsage.
    const usageEl = document.createElement("div");
    usageEl.className = "plugin-sub plugin-usage";
    usageEl.dataset.plugin = m.name;
    card.appendChild(usageEl);

    // Footer: action button(s). A reuses card (漫画翻译) shares another plugin's
    // deps: once available there's nothing to uninstall separately (show a note);
    // when missing, the install button installs the reused plugin.
    const foot = document.createElement("div"); foot.className = "plugin-card-foot";
    if (m.reuses && m.available) {
      const note = document.createElement("span");
      note.className = "plugin-sub";
      note.textContent = _label("Provided by the Image OCR plugin", "随「图像 OCR」插件提供");
      foot.appendChild(note);
    } else {
      const btn = document.createElement("button");
      btn.className = m.available ? "" : "primary";
      btn.innerHTML = (m.available ? "" : ICON.download) +
        `<span>${m.available ? _label("Uninstall", "卸载") : _label("Install", "安装")}</span>`;
      btn.onclick = () => moduleAction(m.reuses || m.name, m.available ? "uninstall" : "install", btn, statEl);
      foot.appendChild(btn);
    }
    card.appendChild(foot);

    grid.appendChild(card);
    if (m.available && !m.reuses) checkModuleUpdate(m.name, foot, statEl);
  }
  loadModuleUsage();
  renderMarket();
}

// ----- plugin market: remote downloadable plugins (not yet installed) -----
async function renderMarket() {
  const grid = $("modules-grid");
  // drop any previous market cards before re-rendering
  grid.querySelectorAll(".plugin-card.market").forEach((c) => c.remove());
  let list = [];
  try { list = (await api("/api/modules/market")).plugins || []; } catch (e) { return; }
  for (const p of list) {
    const card = document.createElement("div");
    card.className = "plugin-card market";
    const head = document.createElement("div"); head.className = "plugin-card-head";
    const ic = document.createElement("span"); ic.className = "plugin-card-icon"; ic.innerHTML = ICON.download;
    const nm = document.createElement("div"); nm.className = "plugin-card-name"; nm.textContent = p.name || p.key;
    const badge = document.createElement("span"); badge.className = "plugin-card-status";
    badge.innerHTML = pill("off", _label("Available", "可下载"), ICON.download);
    head.append(ic, nm, badge); card.appendChild(head);
    const sub = document.createElement("div"); sub.className = "plugin-card-sub";
    sub.textContent = (p.detail || "") + (p.version ? ` · v${p.version}` : "");
    card.appendChild(sub);
    const foot = document.createElement("div"); foot.className = "plugin-card-foot";
    const btn = document.createElement("button"); btn.className = "primary";
    btn.innerHTML = ICON.download + `<span>${_label("Download", "下载")}</span>`;
    btn.onclick = () => downloadMarketPlugin(p, btn);
    foot.appendChild(btn); card.appendChild(foot);
    grid.appendChild(card);
  }
}

async function downloadMarketPlugin(p, btn) {
  btn.disabled = true;
  $("modules-status").textContent = `${_label("Downloading", "正在下载…")} ${p.name || p.key}`;
  try {
    await api("/api/modules/download", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: p.key }) });   // url resolved server-side from the trusted index
  } catch (e) {
    btn.disabled = false;
    $("modules-status").textContent = (e && e.message) || _label("Try Again Later", "请稍后重试");
    return;
  }
  // Refresh bootstrap so the downloaded plugin appears as an installable card.
  try { BOOT = await api("/api/bootstrap"); } catch (e) {}
  renderModules();
  $("modules-status").textContent = `${p.name || p.key}: ${_label("Downloaded Install Below", "已下载，请在上方安装其依赖")}`;
}

function humanSize(n) {
  let s = Number(n) || 0;
  for (const u of ["B", "KB", "MB", "GB", "TB"]) {
    if (s < 1024 || u === "TB") return (u === "B" ? s : s.toFixed(1)) + " " + u;
    s /= 1024;
  }
}

// Fill each plugin's disk-usage line (downloaded models + size) so the user can
// manage space. Lazy (separate from bootstrap) so it never slows page load.
async function loadModuleUsage() {
  let data;
  try { data = await api("/api/modules/usage"); } catch (e) { return; }
  const usage = data.usage || {};
  for (const el of document.querySelectorAll(".plugin-usage")) {
    const u = usage[el.dataset.plugin];
    if (!u) { el.textContent = ""; continue; }
    // Two volumes only: library (pip deps) + models (the model list now lives in
    // the per-model picker).
    const shared = u.shared && u.model_bytes ? `（${_label("Shared", "共用")}）` : "";
    el.textContent = `${_label("Library Size", "库")} ${u.lib_human} · ${_label("Models Size", "模型")} ${u.model_human}${shared}`;
  }
}

// Open the plugin's model MANAGEMENT view: each model shows its install status +
// disk size, with an Install / Delete button (white = installed → Delete, gray =
// not installed → Install). Clicking an installed model makes it the active one
// (the model the plugin uses). The card chip reflects the active model.
function openPluginModelModal(m, chip, chipText) {
  $("plugin-model-title").textContent = m.name;
  $("plugin-model-status").textContent = "";
  const modal = $("plugin-model-modal");
  const switchBtn = $("plugin-model-switch");
  if (switchBtn) switchBtn.style.display = "none";   // per-row actions now
  const cancel = $("plugin-model-cancel");
  if (cancel) cancel.textContent = _label("Close", "关闭");
  const close = () => {
    modal.hidden = true;
    cancel.onclick = null; modal.onclick = null;
    if (switchBtn) switchBtn.style.display = "";
    loadModuleUsage(); renderModules();   // sizes / active may have changed
  };
  cancel.onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  modal.hidden = false;
  refreshPickerRows(m, chipText);
}

async function refreshPickerRows(m, chipText) {
  const list = $("plugin-model-list");
  list.innerHTML = "";
  let data;
  try { data = await api("/api/modules/models?name=" + encodeURIComponent(m.name)); }
  catch (e) { list.textContent = (e.message || "加载失败"); return; }
  m.current_model = data.current_model;
  for (const s of data.models) {
    const row = document.createElement("div");
    row.className = "picker-row" + (s.id === data.current_model ? " active" : "");
    const main = document.createElement("div"); main.className = "picker-main";
    const nm = document.createElement("span"); nm.className = "picker-name"; nm.textContent = s.label;
    main.appendChild(nm);
    (s.tags || []).forEach((t) => {
      const c = document.createElement("span");
      c.className = "model-tag" + (t === "Tag Recommended" ? " rec" : "");
      c.textContent = _label(t, t); main.appendChild(c);
    });
    const size = document.createElement("span"); size.className = "picker-size";
    size.textContent = s.downloaded ? (s.disk_human || "") : _label("Not Installed", "未安装");
    const act = document.createElement("div"); act.className = "picker-act";
    const b = document.createElement("button"); b.className = "mini" + (s.downloaded ? " danger" : "");
    b.textContent = s.downloaded ? _label("Delete", "删除") : _label("Install", "安装");
    b.onclick = (e) => {
      e.stopPropagation();
      if (s.downloaded) pickerDelete(m, s, chipText); else pickerInstall(m, s, chipText);
    };
    act.appendChild(b);
    if (s.downloaded && s.id !== data.current_model) {
      row.style.cursor = "pointer";
      row.onclick = () => pickerActivate(m, s, chipText);
    }
    row.append(main, size, act);
    list.appendChild(row);
  }
}

async function pickerActivate(m, s, chipText) {
  try {
    await api("/api/models/select", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin: m.name, model_id: s.id }),
    });
    if (chipText) chipText.textContent = (s.label || s.id).replace(/\s*[（(].*$/, "").trim();
    refreshPickerRows(m, chipText);
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
}

async function pickerInstall(m, s, chipText) {
  const status = $("plugin-model-status");
  status.innerHTML = pill("busy", _label("Downloading Model", "正在下载模型…") + " " + s.label, "");
  try {
    await api("/api/modules/model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: m.name, model_id: s.id }),
    });
  } catch (e) { status.innerHTML = pill("bad", "失败", ICON.cross); return; }
  const poll = setInterval(async () => {
    let j;
    try { j = await api("/api/modules/status?name=" + encodeURIComponent(m.name)); }
    catch (e) { return; }
    if (j.status === "queued" || j.status === "running") {
      const pct = (j.progress != null) ? " " + Math.round(j.progress * 100) + "%" : "";
      status.innerHTML = pill("busy", (j.status === "queued"
        ? _label("Status Queued", "排队中")
        : _label("Downloading Model", "正在下载模型…")) + pct, "");
      return;
    }
    clearInterval(poll);
    if (j.status === "error") {
      status.innerHTML = pill("bad", _label("Download Failed", "下载失败,请检查网络后重试"), ICON.cross);
    } else {
      status.textContent = "";
    }
    refreshPickerRows(m, chipText);
  }, 1500);
}

async function pickerDelete(m, s, chipText) {
  if (!confirm(_label("Delete Model Confirm", "确定删除该模型的本地文件吗？"))) return;
  try {
    await api("/api/models/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin: m.name, model_id: s.id }),
    });
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
  refreshPickerRows(m, chipText);
}

// For an installed module, ask PyPI (server-side) whether a newer version
// exists; if so, add an "升级" button. Reports only — clicking it confirms.
async function checkModuleUpdate(name, actTd, statTd) {
  let info;
  try {
    info = await api("/api/modules/update-check?name=" + encodeURIComponent(name));
  } catch { return; }
  if (!info || !info.update) return;
  const up = document.createElement("button");
  up.textContent = `升级 (${info.current} → ${info.latest})`;
  up.style.marginLeft = "8px";
  up.onclick = () => moduleAction(name, "upgrade", up, statTd);
  actTd.appendChild(up);
}

const _MODULE_VERBS = { install: "安装", uninstall: "卸载", upgrade: "升级" };

async function moduleAction(name, action, btn, statTd) {
  if (action === "uninstall" &&
      !confirm(_label("Uninstall Models Confirm",
                      "是否一起卸载该插件的模型？被其他插件共用的模型不会删除。"))) {
    return;
  }
  btn.disabled = true;
  const verb = _MODULE_VERBS[action] || action;
  statTd.innerHTML = pill("busy", verb + "中", "");
  try {
    await api("/api/modules/" + action, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }) });
  } catch (e) {
    // e.g. 409 (another op in progress) or network error — re-enable the button so
    // it never gets stuck disabled, and surface the reason.
    btn.disabled = false;
    statTd.innerHTML = pill("bad", "失败", ICON.cross);
    $("modules-status").textContent = `${name}: ` + ((e && e.message) || _label("Try Again Later", "请稍后重试"));
    return;
  }
  const poll = setInterval(async () => {
    let s;
    try {
      s = await api("/api/modules/status?name=" + encodeURIComponent(name));
    } catch (e) { return; }   // transient poll error -> keep polling
    if (s.status === "queued") {
      statTd.innerHTML = pill("busy", _label("Status Queued", "排队中"), "");
      return;
    }
    if (s.status === "running") {
      // Show a PERCENTAGE, not log lines (lib install 0-70%, model 70-100%).
      const pct = (s.progress != null) ? " " + Math.round(s.progress * 100) + "%" : "";
      statTd.innerHTML = pill("busy", verb + "中" + pct, "");
      return;
    }
    clearInterval(poll);
    btn.disabled = false;
    if (s.status === "done") {
      statTd.innerHTML = pill("on", "完成", ICON.check);
      let msg;
      if (action === "uninstall") {
        msg = `${name} ${_label("Cleanup Done", "清理完成")}`;
        if (s.freed_bytes > 0) msg += ` · ${_label("Freed", "已释放")} ${humanSize(s.freed_bytes)}`;
        msg += " —— 请重启程序以生效。";
      } else {
        msg = `${name} ${verb}完成 —— 请重启程序以生效。`;
        if (action === "install" && s.model_failed) {
          statTd.innerHTML = pill("bad", _label("Model Download Failed", "模型下载失败"), ICON.cross);
          msg += " ⚠ " + _label("Model Download Failed Hint",
            "库已安装,但模型下载失败(可能是网络问题)。请重启后在插件页重新下载模型。");
        }
      }
      $("modules-status").textContent = msg;
      // Re-fetch bootstrap so the CARD itself (status badge + install/uninstall
      // button) reflects the new availability — not just the transient pill.
      // (Was the bug: card still showed "未安装" after a successful install.)
      try { BOOT = await api("/api/bootstrap"); } catch (e) {}
      renderModules();
    } else {
      statTd.innerHTML = pill("bad", "失败", ICON.cross);
      $("modules-status").textContent = `${name} 操作失败：` + (s.output || "").slice(-300);
    }
  }, 1500);
}

// ----- glossary editor -----
$("glossary-edit-select").onchange = () => loadGlossaryTable($("glossary-edit-select").value);
let glossaryCols = [];
async function loadGlossaryTable(name) {
  const data = await api("/api/glossary?name=" + encodeURIComponent(name));
  glossaryCols = data.columns;
  const t = $("glossary-table");
  t.innerHTML = "";
  const head = document.createElement("tr");
  for (const c of data.columns) {        // textContent: CSV headers are untrusted
    const th = document.createElement("th");
    th.textContent = c;
    head.appendChild(th);
  }
  t.appendChild(head);
  for (const row of data.rows) addGlossaryRow(row);
  $("glossary-status").textContent = `已加载 ${data.rows.length} 条`;
}
function addGlossaryRow(values) {
  const t = $("glossary-table");
  const tr = document.createElement("tr");
  for (let i = 0; i < glossaryCols.length; i++) {
    const td = document.createElement("td");
    const inp = document.createElement("input");
    inp.type = "text"; inp.value = (values && values[i]) || "";
    td.appendChild(inp); tr.appendChild(td);
  }
  t.appendChild(tr);
}
$("glossary-add-row").onclick = () => addGlossaryRow([]);
$("glossary-save").onclick = async () => {
  const rows = [];
  $("glossary-table").querySelectorAll("tr").forEach((tr, i) => {
    if (i === 0) return;
    rows.push([...tr.querySelectorAll("input")].map((x) => x.value));
  });
  const res = await api("/api/glossary", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: $("glossary-edit-select").value, columns: glossaryCols, rows }) });
  $("glossary-status").textContent = `已保存 ${res.count} 条`;
};

// Refresh BOTH glossary dropdowns (editor + translate page) from a fresh list,
// keeping each one's selection where possible, and select `pick` in the editor.
function refreshGlossarySelects(list, pick) {
  if (list) BOOT.glossaries = list;
  const editSel = $("glossary-edit-select");
  const transSel = $("glossary");
  const target = (pick && BOOT.glossaries.includes(pick)) ? pick
    : (BOOT.glossaries.includes(editSel.value) ? editSel.value : BOOT.glossaries[0]);
  fillSelect(editSel, BOOT.glossaries, target);
  if (transSel) {
    const t = BOOT.glossaries.includes(transSel.value) ? transSel.value : BOOT.glossaries[0];
    fillSelect(transSel, BOOT.glossaries, t);
  }
}

$("glossary-new").onclick = async () => {
  const name = (prompt(_label("New Glossary Prompt", "新词汇表名称：")) || "").trim();
  if (!name) return;
  try {
    const res = await api("/api/glossary/new", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    refreshGlossarySelects(res.glossaries, res.name);
    await loadGlossaryTable(res.name);
    toast(_label("Save", "保存") + ": " + res.name, "ok");
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
};

$("glossary-import").onclick = () => $("glossary-import-file").click();
$("glossary-import-file").onchange = async (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";                     // allow re-importing the same file
  if (!f) return;
  const stem = f.name.replace(/\.[^.]*$/, "");
  const name = (prompt(_label("New Glossary Prompt", "新词汇表名称："), stem) || "").trim();
  if (!name) return;
  try {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("file", f);
    const res = await api("/api/glossary/import", { method: "POST", body: fd });
    refreshGlossarySelects(res.glossaries, res.name);
    await loadGlossaryTable(res.name);
    toast(_label("Import Glossary", "导入词汇表") + ": " + res.name, "ok");
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
};

$("glossary-delete").onclick = async () => {
  const name = $("glossary-edit-select").value;
  if (!name) return;
  const msg = _label("Delete Glossary Confirm", '删除词汇表“{name}”？此操作不可撤销。').replace("{name}", name);
  if (!confirm(msg)) return;
  try {
    const res = await api("/api/glossary/delete", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    refreshGlossarySelects(res.glossaries, null);
    if (BOOT.glossaries.length) await loadGlossaryTable($("glossary-edit-select").value);
    else { $("glossary-table").innerHTML = ""; $("glossary-status").textContent = ""; }
    toast(_label("Delete", "删除") + ": " + name, "ok");
  } catch (e) { toast((e.message || "failed").slice(-160), "bad"); }
};

// ----- proofread -----
let proofreadCols = [];
// Document-list sort: time (newest<->oldest) / name (A->Z <-> Z->A). Clicking
// the active sort flips its direction; clicking the other switches mode to its
// natural default. Mirrors the Qt proofread page.
let pfSortBy = "time", pfSortDesc = true;
function updateProofreadSortUI() {
  const arrow = pfSortDesc ? "↓" : "↑";
  const t = $("pf-time-arrow"), n = $("pf-name-arrow");
  if (t) t.textContent = pfSortBy === "time" ? " " + arrow : "";
  if (n) n.textContent = pfSortBy === "name" ? " " + arrow : "";
  const tb = $("proofread-sort-time"), nb = $("proofread-sort-name");
  if (tb) tb.classList.toggle("active", pfSortBy === "time");
  if (nb) nb.classList.toggle("active", pfSortBy === "name");
}
function toggleProofreadSort(mode) {
  if (pfSortBy === mode) pfSortDesc = !pfSortDesc;
  else { pfSortBy = mode; pfSortDesc = (mode === "time"); }  // time->newest; name->A-Z
  updateProofreadSortUI();
  loadProofreadDocs();
}
async function loadProofreadDocs() {
  const data = await api(`/api/proofread/docs?sort_by=${pfSortBy}&desc=${pfSortDesc}`);
  updateProofreadSortUI();
  fillSelect($("proofread-select"), data.docs.length ? data.docs : ["(无可校对文档)"]);
  if (!data.docs.length) {
    $("proofread-table").innerHTML = "<tr><td style='border:none'>" +
      emptyState(EICON.files, "暂无可校对的文档", "完成一次翻译后会出现在这里。") + "</td></tr>";
    $("proofread-pager").replaceChildren();
    $("proofread-status").textContent = "";
    return;
  }
  // Only build the (potentially large) table the first time — re-clicking the
  // tab just refreshes the doc list, so opening 校对 stays instant.
  if (!$("proofread-table").querySelector("input")) {
    loadProofreadTable(data.docs[0]);
  }
}
$("proofread-select").onchange = () => { if ($("proofread-select").value !== "(无可校对文档)") loadProofreadTable($("proofread-select").value); };
$("proofread-refresh").onclick = loadProofreadDocs;
if ($("proofread-sort-time")) $("proofread-sort-time").onclick = () => toggleProofreadSort("time");
if ($("proofread-sort-name")) $("proofread-sort-name").onclick = () => toggleProofreadSort("name");

// Paginated: only render PAGE rows at a time (a doc can have thousands of
// segments — rendering all at once froze the tab). Edits are written back into
// proofreadRows immediately so they survive paging and are saved in full.
let proofreadRows = [], proofreadPage = 0;
const PROOFREAD_PAGE = 100;

async function loadProofreadTable(name) {
  $("proofread-status").textContent = "加载中…";
  tableSkeleton($("proofread-table"), 6);
  const data = await api("/api/proofread?name=" + encodeURIComponent(name));
  proofreadCols = data.columns;
  proofreadRows = data.rows;
  proofreadPage = 0;
  renderProofreadPage();
  $("proofread-download").hidden = true;
}

function renderProofreadPage() {
  const t = $("proofread-table");
  t.replaceChildren();
  const frag = document.createDocumentFragment();
  const last = proofreadCols.length - 1;
  const head = document.createElement("tr");
  for (const c of proofreadCols) { const th = document.createElement("th"); th.textContent = c; head.appendChild(th); }
  frag.appendChild(head);
  const start = proofreadPage * PROOFREAD_PAGE;
  const end = Math.min(start + PROOFREAD_PAGE, proofreadRows.length);
  for (let r = start; r < end; r++) {
    const row = proofreadRows[r];
    const tr = document.createElement("tr");
    for (let i = 0; i < proofreadCols.length; i++) {
      const td = document.createElement("td");
      const val = row[i] == null ? "" : row[i];
      if (i === last) {
        const inp = document.createElement("input");
        inp.type = "text"; inp.value = val;
        inp.oninput = (e) => { proofreadRows[r][last] = e.target.value; };
        td.appendChild(inp);
      } else { td.textContent = val; }
      tr.appendChild(td);
    }
    frag.appendChild(tr);
  }
  t.appendChild(frag);
  renderProofreadPager(start, end);
}

function renderProofreadPager(start, end) {
  const total = proofreadRows.length;
  const pages = Math.max(1, Math.ceil(total / PROOFREAD_PAGE));
  const pg = $("proofread-pager");
  pg.replaceChildren();
  $("proofread-status").textContent = "";
  if (total <= PROOFREAD_PAGE) { $("proofread-status").textContent = `共 ${total} 行`; return; }
  const mk = (label, disabled, fn) => {
    const b = document.createElement("button");
    b.textContent = label; b.disabled = disabled; if (!disabled) b.onclick = fn;
    return b;
  };
  pg.appendChild(mk("‹ 上一页", proofreadPage === 0, () => { proofreadPage--; renderProofreadPage(); }));
  const info = document.createElement("span");
  info.className = "pager-info";
  info.textContent = `第 ${proofreadPage + 1}/${pages} 页 · 显示 ${start + 1}–${end} / 共 ${total} 行`;
  pg.appendChild(info);
  pg.appendChild(mk("下一页 ›", proofreadPage >= pages - 1, () => { proofreadPage++; renderProofreadPage(); }));
}
$("proofread-save").onclick = async () => {
  const name = $("proofread-select").value;
  // proofreadRows holds the full document with edits applied across all pages.
  const res = await api("/api/proofread", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, rows: proofreadRows }) });
  $("proofread-status").textContent = `已保存（修改 ${res.changed} 行）`;
};
$("proofread-export").onclick = async () => {
  const name = $("proofread-select").value;
  $("proofread-status").textContent = "导出中…";
  try {
    await api("/api/proofread/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }) });
    const dl = $("proofread-download");
    dl.href = "/api/proofread/download?name=" + encodeURIComponent(name);
    dl.hidden = false;
    $("proofread-status").textContent = "导出完成，点击下载。";
  } catch (e) { $("proofread-status").textContent = "导出失败：" + e.message; }
};

// ----- live voice translation (dual mode) -----
//  · local : client VAD (vad-worklet) -> POST /api/live-recognize then
//            /api/live-translate-text (SenseVoice + LLM), source shown first
//  · google: stream 16k PCM over /ws/live-translate (Gemini 3.5 Live Translate)
let liveWS = null, liveCtx = null, liveSrc = null, liveProc = null, liveStream = null;
let playCtx = null, playTime = 0;
let liveMode = "local", liveNode = null, liveRunning = false;
// Floating captions via Document Picture-in-Picture (Chrome 116+).
let pipWin = null, pipMode = "both", pipFont = 24;
// Rolling buffer of recent utterances {src, dst}. The caption window shows as
// many as fit (newest pinned to the bottom), so a big window shows more lines.
let pipEntries = [];
const PIP_MAX = 12;   // buffer for FIFO pairing; only the newest 3 are rendered
let pipInterim = "";   // live, not-yet-finalized source text (streaming)
let liveAnalyser = null, liveLevelRAF = null;

// Mic level feedback: drive the bar + icon from live mic volume so you can see
// you're actually being heard (and whether you're loud enough).
const _WAVE_N = 28;
let _waveHist = new Array(_WAVE_N).fill(0);
function _roundRectPath(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function _drawWave(ctx, cv) {
  const w = cv.width, h = cv.height, n = _WAVE_N;
  ctx.clearRect(0, 0, w, h);
  const gap = 4, bw = Math.max(2, (w - gap * (n - 1)) / n), cy = h / 2;
  for (let i = 0; i < n; i++) {
    const lv = _waveHist[i];
    const bh = Math.max(3, lv * (h - 6));
    ctx.fillStyle = lv < 0.10 ? "#6b7a90" : (lv < 0.88 ? "#22c55e" : "#ef4444");
    _roundRectPath(ctx, i * (bw + gap), cy - bh / 2, bw, bh, bw / 2);
    ctx.fill();
  }
}
function startMicMeter() {
  if (!liveCtx || !liveSrc) return;
  try {
    liveAnalyser = liveCtx.createAnalyser();
    liveAnalyser.fftSize = 1024;
    liveSrc.connect(liveAnalyser);     // tap only; not connected to destination
  } catch (e) { return; }
  const buf = new Float32Array(liveAnalyser.fftSize);
  const cv = $("mic-wave"), ctx = cv && cv.getContext("2d"), icon = $("mic-icon");
  _waveHist = new Array(_WAVE_N).fill(0);
  const tick = () => {
    if (!liveAnalyser) return;
    liveAnalyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const lv = Math.min(1, Math.sqrt(sum / buf.length) * 2.8);
    _waveHist.push(lv); _waveHist.shift();
    if (ctx) _drawWave(ctx, cv);
    if (icon) icon.classList.toggle("speaking", lv > 0.10);
    liveLevelRAF = requestAnimationFrame(tick);
  };
  tick();
}
function stopMicMeter() {
  if (liveLevelRAF) cancelAnimationFrame(liveLevelRAF);
  liveLevelRAF = null;
  try { if (liveAnalyser) liveAnalyser.disconnect(); } catch (e) { /* */ }
  liveAnalyser = null;
  const cv = $("mic-wave"), icon = $("mic-icon");
  if (cv) { const c = cv.getContext("2d"); if (c) c.clearRect(0, 0, cv.width, cv.height); }
  if (icon) icon.classList.remove("speaking");
}

function fillLiveTarget() {
  const sel = $("live-target");
  sel.innerHTML = "";
  for (const name of BOOT.languages) {
    const code = BOOT.language_map[name];
    if (!code) continue;
    const o = document.createElement("option");
    o.value = code; o.textContent = name; sel.appendChild(o);
  }
  sel.value = BOOT.language_map[BOOT.config.default_dst_lang] || "zh";
  refreshLiveInputDevices();
}

// Populate the live "输入" (input source) dropdown: each microphone + (where the
// browser supports it) a system/tab-audio option via getDisplayMedia. Device
// labels only appear after mic permission has been granted once.
async function refreshLiveInputDevices() {
  const sel = $("live-input-dev");
  if (!sel || !navigator.mediaDevices) return;
  const cur = sel.value;
  let devs = [];
  try { devs = await navigator.mediaDevices.enumerateDevices(); } catch (e) { /* */ }
  sel.replaceChildren();
  const def = document.createElement("option");
  def.value = ""; def.textContent = _label("Default Microphone", "默认麦克风");
  sel.appendChild(def);
  let n = 0;
  for (const d of devs) {
    if (d.kind !== "audioinput") continue;
    n += 1;
    const o = document.createElement("option");
    o.value = d.deviceId; o.textContent = d.label || `${_label("Input", "输入")} ${n}`;
    sel.appendChild(o);
  }
  if (navigator.mediaDevices.getDisplayMedia) {
    const o = document.createElement("option");
    o.value = "__system__";
    o.textContent = _label("System / Tab Audio (share)", "系统/标签页声音（需共享）");
    o.title = _label(
      "Browsers can only capture system audio via screen-share. Pick Entire Screen and tick 'Share system audio' (or share a tab and tick 'Share tab audio').",
      "浏览器只能通过“共享屏幕”捕获系统声音：请选「整个屏幕」并勾选「分享系统音频」（或共享某标签页并勾选「分享标签页音频」）。");
    sel.appendChild(o);
  }
  if (cur && [...sel.options].some((o) => o.value === cur)) sel.value = cur;
}
if (navigator.mediaDevices) {
  navigator.mediaDevices.ondevicechange = () => refreshLiveInputDevices();
}

// Acquire the live audio stream from the chosen input: a specific mic, the
// default mic, or system/tab audio (getDisplayMedia, audio-only).
async function acquireLiveStream() {
  const dev = $("live-input-dev") ? $("live-input-dev").value : "";
  if (dev === "__system__") {
    // Browsers expose system/tab audio ONLY through getDisplayMedia. `video`
    // is required (Chrome won't offer audio without a surface) but we keep just
    // the audio track. No displaySurface hint, so the picker offers Tab / Window
    // / Entire-screen equally — sharing the playing TAB (with "share tab audio")
    // is enough for one webpage; "Entire screen + share system audio" grabs all.
    const s = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      systemAudio: "include",
      selfBrowserSurface: "exclude",
      preferCurrentTab: false,
    });
    s.getVideoTracks().forEach((t) => t.stop());   // keep audio only
    if (!s.getAudioTracks().length) {
      s.getTracks().forEach((t) => t.stop());
      throw new Error("未捕获到音频：分享时务必勾选「分享标签页音频」（共享某个标签页时，复选框在弹窗左下角），或「分享系统音频」（共享整个屏幕时）。");
    }
    return s;
  }
  const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
  if (dev) audio.deviceId = { exact: dev };
  const s = await navigator.mediaDevices.getUserMedia({ audio });
  // First grant reveals device labels — refresh so the dropdown shows names.
  refreshLiveInputDevices();
  return s;
}
// Save the just-finished session (source + translation) to history on stop.
function saveLiveHistory() {
  const lines = (id) => (($(id) && $(id).textContent) || "")
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const src = lines("live-input"), dst = lines("live-output");
  if (!src.length && !dst.length) return;
  const tsel = $("live-target");
  const dstDisplay = tsel ? (tsel.options[tsel.selectedIndex] || {}).text || "" : "";
  api("/api/live-save-history", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_lines: src, translated_lines: dst,
      src_display: "Auto", dst_display: dstDisplay,
      tokens: liveSessionTokens, ui_lang: _uiLang }) })
    .then((r) => { if (r && r.saved) showThanks(r.tokens, r.cost); })
    .catch(() => {});
}
function setLiveStatus(t) { $("live-status").textContent = t; }
// "Listening" wording adapts to the input (mic vs shared system/tab audio).
function liveListenMsg() {
  const sel = $("live-input-dev");
  return (sel && sel.value === "__system__")
    ? "正在聆听（系统/标签页声音）…"
    : "正在聆听…（对着麦克风说话）";
}
function setLiveBusy(b) { const g = $("live-go"); if (g) g.classList.toggle("running", b); }

// Hot-switch input device mid-session; floating-caption (PiP) toggle.
function onLiveInputChange() {
  const sel = $("live-input-dev");
  if (sel && sel.value === "__system__") {
    setLiveStatus("翻译某个网页的声音：开始后在共享框选「该标签页」并勾选「分享标签页音频」（无需整个屏幕）。翻译整机声音：选「整个屏幕」并勾「分享系统音频」。");
  }
  switchLiveInput();
}
if ($("live-input-dev")) $("live-input-dev").onchange = onLiveInputChange;
if ($("live-pip")) {
  $("live-pip").onclick = toggleLivePip;
  if (!("documentPictureInPicture" in window)) $("live-pip").style.display = "none";
}

// Mode switch (disabled while a session is running).
document.querySelectorAll("#live-mode .seg").forEach((s) => {
  s.onclick = () => {
    if (liveRunning) return;
    document.querySelectorAll("#live-mode .seg").forEach((x) => x.classList.remove("active"));
    s.classList.add("active");
    liveMode = s.dataset.mode;
    updateLiveHint();
  };
});

// Show a hint when the chosen mode isn't ready (no plugin / no Google key).
let _liveHintIsPlugin = false;
async function updateLiveHint() {
  let msg = "";
  _liveHintIsPlugin = false;
  if (liveMode === "local") {
    if (!BOOT.local_live_available) {
      msg = "本地模式需要「语音」插件（SenseVoice）。点此前往「插件」安装。";
      _liveHintIsPlugin = true;
    }
  } else {
    const st = await api("/api/apikey?model=" + encodeURIComponent("(Google) Live Translate")).catch(() => ({ has_key: false }));
    if (!st.has_key) msg = "Google 实时翻译需要 Google API Key。请在「接口管理」中填写。";
  }
  $("live-hint-text").textContent = msg;
  $("live-hint").hidden = !msg;
  $("live-hint").style.cursor = _liveHintIsPlugin ? "pointer" : "default";
  return !msg;  // ready?
}
// One-click jump to the Plugins page when the hint is about a missing plugin.
if ($("live-hint")) $("live-hint").onclick = () => {
  if (_liveHintIsPlugin) { const t = document.querySelector('.tab[data-tab="modules"]'); if (t) t.click(); }
};

// One round button toggles start/stop (green play -> red stop).
$("live-go").onclick = async () => {
  if (liveRunning) { if (liveMode === "google") stopGoogle(); else stopLocal(); return; }
  if (!(await updateLiveHint())) return;   // blocked: hint already shown
  if (liveMode === "google") startGoogle(); else startLocal();
};

// Show the model the live translation will use (the active interface's).
function refreshLiveModel() {
  const el = $("live-model");
  if (el) el.textContent = (BOOT.config && BOOT.config.default_online_model) || "—";
}

// --- Google (Gemini Live): continuous 16k PCM over WS, plays 24k reply audio ---
async function startGoogle() {
  try {
    liveStream = await acquireLiveStream();
  } catch (e) { setLiveStatus("无法访问输入设备：" + e.message); return; }
  $("live-input").textContent = ""; $("live-output").textContent = "";
  liveCtx = new AudioContext();
  const srcRate = liveCtx.sampleRate;
  liveSrc = liveCtx.createMediaStreamSource(liveStream);
  liveProc = liveCtx.createScriptProcessor(4096, 1, 1);
  playCtx = new AudioContext({ sampleRate: 24000 }); playTime = 0;

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  liveWS = new WebSocket(`${proto}//${location.host}/ws/live-translate?target=${encodeURIComponent($("live-target").value)}`);
  liveWS.onopen = () => setLiveStatus(liveListenMsg());
  liveWS.onmessage = onLiveMessage;
  liveWS.onclose = () => { setLiveStatus("连接已关闭"); liveRunning = false; setLiveBusy(false); };
  liveWS.onerror = () => setLiveStatus("连接错误");

  liveProc.onaudioprocess = (e) => {
    if (!liveWS || liveWS.readyState !== 1) return;
    liveWS.send(JSON.stringify({ audio: int16ToB64(downsamplePCM16(e.inputBuffer.getChannelData(0), srcRate)) }));
  };
  liveSrc.connect(liveProc); liveProc.connect(liveCtx.destination);
  liveRunning = true; setLiveBusy(true); startMicMeter();
}
function stopGoogle() {
  saveLiveHistory();
  stopMicMeter();
  try { if (liveProc) liveProc.disconnect(); if (liveSrc) liveSrc.disconnect(); } catch (e) { /* */ }
  if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
  if (liveWS && liveWS.readyState === 1) { try { liveWS.send(JSON.stringify({ end: true })); } catch (e) {} liveWS.close(); }
  if (liveCtx) { try { liveCtx.close(); } catch (e) {} liveCtx = null; }
  // Also release the 24k playback context (browsers cap live AudioContexts; not
  // closing it leaked one per Google session until the cap threw).
  if (playCtx) { try { playCtx.close(); } catch (e) {} playCtx = null; }
  liveRunning = false; setLiveBusy(false); setLiveStatus("已停止");
}

// --- Local (SenseVoice + LLM): audio-thread VAD segments -> POST per utterance ---
// A friendly "thanks for using LinguaHaru" card shown when an experience
// finishes (document translation done, real-time voice stopped), summarizing
// the tokens used + estimated cost. `cost` is {amount, symbol, currency} or null.
const THANKS_COOLDOWN_MS = 10 * 60 * 1000;   // pop the card at most once per 10 min
function showThanks(tokens, cost) {
  // Shown when a LONG task finishes (document translation, real-time voice).
  // Skipped when there are no tokens to report, and throttled so frequent runs
  // don't pop a card every time. Quick Translate never calls this.
  if (!tokens) return;
  try {
    const last = +(localStorage.getItem("lh-thanks-last") || 0);
    if (Date.now() - last < THANKS_COOLDOWN_MS) return;   // within cooldown -> skip
    localStorage.setItem("lh-thanks-last", String(Date.now()));
  } catch (e) { /* localStorage unavailable -> just show */ }
  const old = document.getElementById("thanks-overlay");
  if (old) old.remove();
  const fmtTokens = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n));
  const ov = document.createElement("div");
  ov.id = "thanks-overlay"; ov.className = "thanks-overlay";
  const tokenLine = tokens
    ? `<div class="thanks-stat"><span>${_label("Thanks Tokens Label", "本次消耗")}</span><b>${fmtTokens(tokens)} tokens</b></div>`
    : "";
  const costLine = cost
    ? `<div class="thanks-stat"><span>${_label("Thanks Cost Label", "预计花费")}</span><b>${cost.symbol}${cost.amount} ${cost.currency}</b></div>`
    : "";
  ov.innerHTML =
    `<div class="thanks-card">
       <div class="thanks-flower">✿</div>
       <h3>${_label("Thanks Title", "感谢使用 LinguaHaru")}</h3>
       ${tokenLine}
       ${costLine}
       <button id="thanks-ok">${_label("OK", "好的")}</button>
     </div>`;
  ov.addEventListener("click", (e) => { if (e.target === ov || e.target.id === "thanks-ok") ov.remove(); });
  document.body.appendChild(ov);
}

// Glass lock shown while an STT model loads (first use can download + warm for
// seconds). Blocks mis-clicks and tells the user what's happening.
function showModelLoading(text) {
  const o = $("model-loading-overlay"); if (!o) return;
  const t = $("model-loading-text"); if (t && text) t.textContent = text;
  o.hidden = false;
}
function hideModelLoading() {
  const o = $("model-loading-overlay"); if (o) o.hidden = true;
}

async function startLocal() {
  liveSessionTokens = 0;
  // Create the AudioContext NOW, while we're still inside the click's user
  // gesture. If we create it AFTER the model-preload await below (which can take
  // many seconds), the gesture activation is gone and the context starts
  // 'suspended' — no audio flows and the user has to click a second time. (Qt
  // has no browser gesture rule, which is why only Web showed this.)
  liveCtx = new AudioContext();
  try {
    liveStream = await acquireLiveStream();
  } catch (e) {
    setLiveStatus("无法访问输入设备：" + e.message);
    try { liveCtx.close(); } catch (e2) {} liveCtx = null; return;
  }
  $("live-input").textContent = ""; $("live-output").textContent = "";
  // Preload the local model so the first sentence isn't blocked on a slow load.
  setLiveStatus("正在加载本地模型…（首次需下载/加载，请稍候）");
  showModelLoading("正在加载语音模型…\n首次使用需下载并载入，请稍候");
  try { await api("/api/live-preload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "live" }) }); }
  catch (e) { /* load lazily */ }
  finally { hideModelLoading(); }
  // The long await may have dropped the gesture activation; ensure the context
  // is actually running before wiring up the mic graph.
  if (liveCtx.state === "suspended") { try { await liveCtx.resume(); } catch (e) {} }
  liveSrc = liveCtx.createMediaStreamSource(liveStream);
  const useSilero = !!(BOOT.config && BOOT.config.web_vad === "silero");
  try {
    if (useSilero) await startSileroVad();
    else await startWorkletVad();
  } catch (e) {
    if (useSilero) {   // neural VAD failed (network/CDN?) -> fall back to energy
      setLiveStatus("Silero VAD 不可用（" + e.message + "），回退能量 VAD…");
      try { await startWorkletVad(); }
      catch (e2) { setLiveStatus("VAD 初始化失败：" + e2.message); stopLocal(); return; }
    } else { setLiveStatus("VAD 初始化失败：" + e.message); stopLocal(); return; }
  }
  liveRunning = true; setLiveBusy(true); setLiveStatus(liveListenMsg());
  liveCtxHistory = [];   // fresh coherence context per session
  startMicMeter();
}
// Mic sensitivity preset -> [onset, end-of-speech] energy thresholds. Lower =
// more sensitive (picks up softer speech); higher = needs a louder voice.
const VAD_SENS = { high: [0.004, 0.0026], standard: [0.006, 0.004], low: [0.010, 0.0066] };
async function startWorkletVad() {
  await liveCtx.audioWorklet.addModule("/static/vad-worklet.js?v=20260616A");
  const cfg = BOOT.config || {};
  const hangMs = cfg.live_vad_hang_ms || 900;
  const maxSegMs = cfg.live_vad_max_seg_ms || 30000;
  const [onAbs, offAbs] = VAD_SENS[cfg.live_vad_sensitivity] || VAD_SENS.standard;
  liveNode = new AudioWorkletNode(liveCtx, "vad-processor",
    { processorOptions: { prerollMs: 500, onMs: 90, hangMs, minSegMs: 280, maxSegMs,
                          onAbs, offAbs } });
  liveNode.port.onmessage = onVadMessage;
  liveNode.port.postMessage({ type: "mode", mode: "open" });
  liveSrc.connect(liveNode); liveNode.connect(liveCtx.destination);
}
// Neural VAD (Silero via onnxruntime-web) — same robustness tier as Qt's TEN-VAD.
// Uses AudioNodeVAD on our acquired stream so the input picker / system audio
// still works; feeds growing partials via onFrameProcessed (stable-prefix).
let liveSilero = null, liveSileroBuf = [], liveSileroLast = 0, liveSileroSpeaking = false;
let _vadLibsP = null;
function loadVadLibs() {
  if (_vadLibsP) return _vadLibsP;
  const L = (s) => new Promise((res, rej) => {
    const e = document.createElement("script"); e.src = s; e.onload = res;
    e.onerror = () => rej(new Error("加载失败")); document.head.appendChild(e);
  });
  _vadLibsP = (async () => {
    // Self-hosted (same-origin) so the page's COEP:require-corp (needed for the
    // in-browser ffmpeg.wasm) doesn't block the onnxruntime-web WASM/worker.
    try { await api("/api/ensure-web-vad", { method: "POST" }); } catch (e) { /* assets may already exist */ }
    if (!window.ort) await L("/static/vad/ort.min.js");
    if (window.ort && window.ort.env) window.ort.env.wasm.wasmPaths = "/static/vad/";
    if (!window.vad) await L("/static/vad/bundle.min.js");
  })();
  return _vadLibsP;
}
function floatToInt16(f32) {
  const o = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) { const s = Math.max(-1, Math.min(1, f32[i] || 0)); o[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
  return o;
}
function floatsToInt16(chunks) {
  let n = 0; for (const c of chunks) n += c.length;
  const all = new Float32Array(n); let o = 0;
  for (const c of chunks) { all.set(c, o); o += c.length; }
  return floatToInt16(all);
}
async function startSileroVad() {
  await loadVadLibs();
  liveSileroBuf = []; liveSileroLast = 0; liveSileroSpeaking = false;
  liveSilero = await window.vad.AudioNodeVAD.new(liveCtx, {
    baseAssetPath: "/static/vad/", onnxWASMBasePath: "/static/vad/", model: "legacy",
    onSpeechStart() {
      liveSileroSpeaking = true; liveCommittedText = ""; liveLastText = ""; livePendingPcm = null; pipInterim = "";
      liveSileroBuf = []; liveSileroLast = performance.now(); setLiveStatus("识别中…");
    },
    onFrameProcessed(_probs, frame) {
      if (!liveSileroSpeaking || !frame || !frame.length) return;
      liveSileroBuf.push(new Float32Array(frame));
      const now = performance.now();
      if (now - liveSileroLast > 360) { liveSileroLast = now; streamPartial(floatsToInt16(liveSileroBuf)); }
    },
    onSpeechEnd(audio) {
      liveSileroSpeaking = false;
      finalizeUtterance(floatToInt16(audio)); liveSileroBuf = [];
    },
  });
  liveSilero.receive(liveSrc);
  liveSilero.start();
}
function stopLocal() {
  saveLiveHistory();
  stopMicMeter();
  try {
    if (liveSilero) { liveSilero.pause(); liveSilero.destroy(); }
    if (liveNode) { if (liveNode.port) liveNode.port.postMessage({ type: "mode", mode: "block" }); liveNode.disconnect(); }
    if (liveSrc) liveSrc.disconnect();
  } catch (e) { /* */ }
  if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
  if (liveCtx) liveCtx.close();
  liveSilero = null; liveSileroSpeaking = false; liveNode = null;
  liveRunning = false; setLiveBusy(false); setLiveStatus("已停止");
}
// --- Streaming live recognition (Windows-Live-Captions style) ---------------
// Within one VAD utterance the worklet sends growing `partial` audio; we re-run
// STT on it (latest-wins, one in flight), and use STABLE-PREFIX commit: a
// sentence is finalized & translated the moment the NEXT sentence starts to
// appear — so sentence 1 is translated while you're already saying sentence 2.
let liveCommittedText = "";  // raw text prefix already committed this utterance
let liveLastText = "";       // previous partial (for LocalAgreement-2 stable prefix)
let liveCtxHistory = [];     // recent committed source lines (translation coherence context)
let liveSessionTokens = 0;   // accumulated tokens this live session (for the thanks card)

// Recent committed source as disambiguation context (last 3 lines / ~200 chars).
function liveContext() {
  if (!liveCtxHistory.length) return "";
  return liveCtxHistory.slice(-3).join(" ").slice(-200);
}
let livePartialBusy = false, livePendingPcm = null;
let liveLastDetected = "auto";

function onVadMessage(e) {
  const m = e.data || {};
  if (m.type === "speechstart") {
    liveCommittedText = ""; liveLastText = ""; livePendingPcm = null; pipInterim = "";
    setLiveStatus("识别中…");
  } else if (m.type === "partial") {
    streamPartial(downsamplePCM16(new Float32Array(m.pcm), m.sampleRate));
  } else if (m.type === "segment") {
    finalizeUtterance(downsamplePCM16(new Float32Array(m.pcm), m.sampleRate));
  }
}
function liveTimeStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
// --- Scored sentence boundaries + LocalAgreement-2 prefix commit -------------
// Streaming STT revises its tail and re-segments as more audio arrives, so
// committing by SENTENCE COUNT (slice(emitted)) duplicates/drops sentences when
// boundaries shift. Instead we track a COMMITTED CHARACTER PREFIX and only
// commit text that two consecutive partials AGREE on (LocalAgreement-2 — the
// policy from whisper_streaming / WhisperLiveKit). Within the stable,
// not-yet-committed text we pick the most NATURAL break — sentence punctuation >
// clause comma > space > CJK connective — and only HARD-cut as a last resort.
// Length is measured in CELLS (CJK = 2, latin = 1) so a caption line looks
// balanced regardless of script.
const _SENT_END = "。！？!?.";
const _CLAUSE = "、，,；;";
const _CONNECTIVES = ["然后", "然後", "但是", "所以", "因为", "因為", "如果", "不过",
  "不過", "而且", "还有", "還有", "其实", "其實", "因此", "于是", "於是", "可是",
  "虽然", "雖然", "这样", "這樣"];
const _MIN_CELLS = 24, _TARGET_CELLS = 60, _HARD_CELLS = 88;
function _cell(ch) { return /[　-鿿＀-￯]/.test(ch) ? 2 : 1; }
function _connAt(text, i) { return _CONNECTIVES.some((w) => text.startsWith(w, i)); }
// Index to end the unit starting at `start`, or -1 to wait for more text.
function _findBoundary(text, start, final) {
  let w = 0, comma = -1, space = -1, conn = -1;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    w += _cell(ch);
    if (_SENT_END.includes(ch)) return i + 1;            // best: a full stop
    if (w >= _MIN_CELLS) {
      if (_CLAUSE.includes(ch)) comma = i + 1;
      else if (ch === " ") space = i + 1;
      else if (i > start && _connAt(text, i)) conn = i;   // cut BEFORE the connective
    }
    if (w >= _HARD_CELLS) {                                // last resort: must break
      if (comma > 0) return comma;
      if (space > 0) return space;
      if (conn > 0) return conn;
      return i + 1;                                        // hard cut (no boundary at all)
    }
  }
  // End of available text: commit early only past TARGET at a natural boundary,
  // else wait for more audio (final flushes the remainder later).
  if (!final && w >= _TARGET_CELLS) {
    if (comma > 0) return comma;
    if (space > 0) return space;
    if (conn > 0) return conn;
  }
  return -1;
}
// Split `text` into ready units; `consumed` is the RAW length forming complete
// units (so the caller can advance its committed prefix exactly). When final,
// the trailing remainder is flushed as a last unit.
function splitScored(text, final) {
  const units = [];
  let i = 0;
  while (i < text.length) {
    const cut = _findBoundary(text, i, final);
    if (cut < 0) break;
    const seg = text.slice(i, cut).trim();
    if (seg) units.push(seg);
    i = cut;
  }
  let consumed = i;
  if (final) {
    const seg = text.slice(i).trim();
    if (seg) units.push(seg);
    consumed = text.length;
  }
  return { units, consumed };
}
function commonPrefix(a, b) {
  let i = 0; const n = Math.min(a.length, b.length);
  while (i < n && a[i] === b[i]) i++;
  return a.slice(0, i);
}
// The part of a hypothesis NOT covered by the already-committed text. The STT
// re-decodes the whole window each pass and may REWRITE the utterance HEAD
// ("就比如说…" -> "大哥就比如说…"); the old prefix-rollback then re-committed
// (duplicated) the entire utterance. Align by: exact prefix -> content probe
// (last ~10 committed chars, punctuation-stripped: re-decodes love flipping
// 。<->， at the seam) -> length skip. Twin of Qt live_page._uncommitted_tail.
const SEAM_PUNCT = /^[。！？!?.，、,；;\s]+/;
function uncommittedTail(text, committed) {
  if (!committed) return text;
  let tail;
  if (text.startsWith(committed)) {
    tail = text.slice(committed.length);
  } else {
    const probe = committed.slice(-10).replace(/[。！？!?.，、,；;\s]+$/, "");
    const i = probe ? text.indexOf(probe) : -1;
    tail = i >= 0 ? text.slice(i + probe.length) : text.slice(committed.length);
  }
  return tail.replace(SEAM_PUNCT, "");   // alignment seams leave stray punct
}
// A committable unit needs at least one WORD char (latin/digit, kana, CJK,
// hangul, cyrillic, thai) — SenseVoice emits bare "。"/"?" for noise.
const WORDLIKE = /[0-9A-Za-z぀-ヿ㐀-鿿가-힯Ѐ-ӿ฀-๿]/;
async function recognizeInt16(int16, final) {
  const body = { audio_b64: int16ToB64(int16), final: !!final };
  return await api("/api/live-recognize", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
}
async function streamPartial(int16) {
  if (livePartialBusy) { livePendingPcm = int16; return; }   // latest-wins
  livePartialBusy = true;
  try {
    const r = await recognizeInt16(int16, false);
    if (r.busy) return;   // server dropped this partial under load — keep state, retry next
    liveLastDetected = r.detected || liveLastDetected;
    const text = r.source || "";
    // LocalAgreement-2 on the UNCOMMITTED TAIL: only tail text two consecutive
    // partials agree on is committed. Committed text is NEVER re-emitted, even
    // when the STT rewrites the utterance head (no more duplicated captions).
    const tail = uncommittedTail(text, liveCommittedText);
    const lastTail = liveLastText ? uncommittedTail(liveLastText, liveCommittedText) : "";
    liveLastText = text;
    const stable = commonPrefix(tail, lastTail);
    const { units, consumed } = splitScored(stable, false);
    for (const u of units) commitLiveSentence(u);
    liveCommittedText += stable.slice(0, consumed);
    pipInterim = tail.slice(consumed);      // live, not-yet-committed tail
    updatePipCaption();
    if (liveRunning && pipInterim) setLiveStatus("识别中：" + pipInterim);
  } catch (e) { /* transient; next partial retries */ }
  finally {
    livePartialBusy = false;
    if (livePendingPcm) { const p = livePendingPcm; livePendingPcm = null; streamPartial(p); }
  }
}
async function finalizeUtterance(int16) {
  livePendingPcm = null;
  try {
    const r = await recognizeInt16(int16, true);
    liveLastDetected = r.detected || liveLastDetected;
    const text = r.source || "";
    // Flush only the not-yet-committed tail (aligned even across head rewrites
    // — never re-emits committed text, never drops the tail).
    const { units } = splitScored(uncommittedTail(text, liveCommittedText), true);
    for (const u of units) commitLiveSentence(u);
  } catch (e) { /* drop */ }
  liveCommittedText = ""; liveLastText = ""; pipInterim = ""; updatePipCaption();
  if (liveRunning) setLiveStatus(liveListenMsg());
}
// Finalize one sentence: show the source line now, then translate it. Commits
// are serialized through a promise chain so the optional stream mode can safely
// grow the last output line without races between overlapping sentences.
let liveCommitChain = Promise.resolve();
function commitLiveSentence(source) {
  source = (source || "").trim();
  if (!source || !WORDLIKE.test(source)) return;   // drop bare-punct noise units
  liveCommitChain = liveCommitChain.then(() => _doCommitSentence(source));
  return liveCommitChain;
}
async function _doCommitSentence(source) {
  const ts = liveTimeStamp();
  appendLive("live-input", `[${ts}] ${source}\n`);
  const ctx = liveContext();
  liveCtxHistory.push(source);                 // after building ctx (don't include self)
  if (liveCtxHistory.length > 6) liveCtxHistory.splice(0, liveCtxHistory.length - 6);
  const streaming = !!(BOOT.config && BOOT.config.live_stream_translation);
  const dst = $("live-target").value;
  if (!streaming) {
    try {
      const t = await api("/api/live-translate-text", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, src_lang: liveLastDetected || "auto", dst_lang: dst, context: ctx }) });
      liveSessionTokens += (t.tokens || 0);
      if (t.translated) appendLive("live-output", `[${ts}] ${t.translated}\n`);
    } catch (e) { /* leave source line; translation failed */ }
    return;
  }
  // Stream mode: grow the last output line + a PiP entry token-by-token.
  const out = $("live-output");
  const start = out.textContent.length;
  out.textContent += `[${ts}] \n`;
  const entry = { src: source, dst: "" };
  pipEntries.push(entry);
  if (pipEntries.length > PIP_MAX) pipEntries.splice(0, pipEntries.length - PIP_MAX);
  const paint = (txt) => {
    out.textContent = out.textContent.slice(0, start) + `[${ts}] ${txt}\n`;
    out.scrollTop = out.scrollHeight;
    entry.dst = txt; updatePipCaption();
  };
  try {
    const resp = await fetch("/api/live-translate-stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, src_lang: liveLastDetected || "auto", dst_lang: dst, context: ctx }) });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n"); buf = parts.pop();
      for (const p of parts) {
        const i = p.indexOf("data: "); if (i < 0) continue;
        const d = p.slice(i + 6);
        if (d === "[DONE]") continue;
        let txt; try { txt = JSON.parse(d); } catch (e) { continue; }
        if (txt && typeof txt === "object" && txt.__usage__ != null) {
          liveSessionTokens += (txt.__usage__ || 0);   // streamed line's token cost
          continue;
        }
        paint(txt);
      }
    }
  } catch (e) { /* leave whatever streamed */ }
}

function downsamplePCM16(input, srcRate) {
  if (srcRate === 16000) {            // no resample needed
    const out0 = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i] || 0));
      out0[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out0;
  }
  // Box-average each output sample over its source window (cheap anti-aliasing —
  // nearest-neighbor at 48k->16k aliases and hurts VAD/STT accuracy).
  const ratio = srcRate / 16000, outLen = Math.floor(input.length / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio), end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0, n = 0;
    for (let j = start; j < end; j++) { sum += input[j] || 0; n++; }
    const s = Math.max(-1, Math.min(1, n ? sum / n : 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}
function int16ToB64(int16) {
  const bytes = new Uint8Array(int16.buffer);
  let bin = ""; for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
function onLiveMessage(ev) {
  let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
  if (d.type === "error") { setLiveStatus("错误：" + d.message); return; }
  const sc = d.serverContent;
  if (!sc) return;
  if (sc.inputTranscription && sc.inputTranscription.text) appendLive("live-input", sc.inputTranscription.text);
  if (sc.outputTranscription && sc.outputTranscription.text) appendLive("live-output", sc.outputTranscription.text);
  if (sc.modelTurn && sc.modelTurn.parts) {
    for (const p of sc.modelTurn.parts) if (p.inlineData && p.inlineData.data) playPCM24k(p.inlineData.data);
  }
}
function appendLive(id, text) {
  const el = $(id); el.textContent += text; el.scrollTop = el.scrollHeight;
  // Feed the floating-caption buffer with the latest line (timestamp stripped).
  const last = (el.textContent.trim().split("\n").filter(Boolean).pop() || "")
    .replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, "");
  if (!last) return;
  if (id === "live-input") {
    pipEntries.push({ src: last, dst: "" });
  } else if (id === "live-output") {
    // Pair with the OLDEST source still waiting (FIFO): translations run one
    // request per sentence, so with fast speech 2+ sources are pending at once
    // — pairing with the newest attached sentence N's translation to N+1.
    const open = pipEntries.find((e) => e.src && !e.dst);
    if (open) open.dst = last;
    else pipEntries.push({ src: "", dst: last });
  }
  if (pipEntries.length > PIP_MAX) pipEntries.splice(0, pipEntries.length - PIP_MAX);
  updatePipCaption();
}

// --- Live input hot-switch: change device mid-session without stopping ---
async function switchLiveInput() {
  if (!liveRunning) return;             // not running -> selection applies on next start
  let newStream;
  try { newStream = await acquireLiveStream(); }
  catch (e) { setLiveStatus("无法切换输入：" + e.message); return; }
  try {
    if (liveSrc) liveSrc.disconnect();
    if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
    liveStream = newStream;
    liveSrc = liveCtx.createMediaStreamSource(liveStream);
    if (liveSilero) liveSilero.receive(liveSrc);            // neural VAD
    else liveSrc.connect(liveMode === "google" ? liveProc : liveNode);
    stopMicMeter(); startMicMeter();   // re-bind the level meter to the new stream
    setLiveStatus("已切换输入 · 正在聆听…");
  } catch (e) { setLiveStatus("切换输入失败：" + e.message); }
}

// --- Floating captions (Document Picture-in-Picture, Chrome 116+) ---
// Renders the rolling buffer; the window is a scroll container pinned to the
// bottom, so resizing it bigger simply reveals more lines (no fixed line count).
function updatePipCaption() {
  if (!pipWin || pipWin.closed) return;
  const cap = pipWin.document.getElementById("cap");
  if (!cap) return;
  const d = pipWin.document;
  cap.textContent = "";
  // Google-Meet / 讯飞 style: only the newest few pairs (current speech + a
  // little context); the newest translation bright, older ones dimmed. The full
  // transcript stays in the main page's panels.
  const shown = pipEntries.slice(-3);
  shown.forEach((e, idx) => {
    const newest = idx === shown.length - 1;
    const block = d.createElement("div"); block.className = "blk";
    if (pipMode !== "dst" && e.src) {
      const s = d.createElement("div"); s.className = "c-src";
      s.style.fontSize = Math.round(pipFont * 0.7) + "px";
      s.textContent = e.src; block.appendChild(s);
    }
    if (pipMode !== "src" && e.dst) {
      const t = d.createElement("div"); t.className = "c-dst";
      t.style.fontSize = pipFont + "px";
      if (!newest) t.style.opacity = "0.72";
      t.textContent = e.dst; block.appendChild(t);
    }
    if (block.childNodes.length) cap.appendChild(block);
  });
  // Live interim (still being spoken) — dim, shown when source is visible.
  if (pipInterim && pipMode !== "dst") {
    const it = d.createElement("div"); it.className = "c-src";
    it.style.fontSize = Math.round(pipFont * 0.7) + "px";
    it.style.opacity = "0.6"; it.textContent = pipInterim + " …";
    cap.appendChild(it);
  }
  // Empty state so the floating window isn't a blank, confusing box.
  if (!cap.childNodes.length) {
    const ph = d.createElement("div");
    ph.style.cssText = "opacity:.5;font-size:14px;line-height:1.5;";
    ph.textContent = _label("🎙 Live captions will appear here once you start speaking.",
                            "🎙 开始说话后，实时字幕会显示在这里。");
    cap.appendChild(ph);
  }
  cap.scrollTop = cap.scrollHeight;   // keep newest visible
}
async function toggleLivePip() {
  if (!("documentPictureInPicture" in window)) {
    setLiveStatus("此浏览器不支持悬浮字幕（需 Chrome 116+）"); return;
  }
  if (pipWin && !pipWin.closed) { pipWin.close(); pipWin = null; return; }
  try { pipWin = await window.documentPictureInPicture.requestWindow({ width: 560, height: 240 }); }
  catch (e) { setLiveStatus("无法打开悬浮字幕：" + e.message); return; }
  const d = pipWin.document;
  const st = d.createElement("style");
  st.textContent = "html,body{height:100%;}"
    + "body{margin:0;display:flex;flex-direction:column;font-family:system-ui,'Noto Sans SC',sans-serif;background:rgba(16,18,24,.92);color:#fff;}"
    + ".bar{flex:none;display:flex;gap:6px;justify-content:flex-end;padding:6px 8px;}"
    + ".bar button{background:rgba(255,255,255,.14);color:#fff;border:none;border-radius:6px;padding:3px 9px;cursor:pointer;font-size:12px;}"
    + "#cap{flex:1;overflow-y:auto;padding:2px 14px 14px;scrollbar-width:thin;}"
    + ".blk{margin:0 0 10px;}.c-src{color:#b8c0cc;line-height:1.4;}.c-dst{font-weight:700;line-height:1.45;margin-top:2px;}";
  d.head.appendChild(st);
  const label = () => ({ both: _label("Bilingual", "双语"), dst: _label("Translation Only", "仅译文"), src: _label("Source Only", "仅原文") }[pipMode]);
  const bar = d.createElement("div"); bar.className = "bar";
  const mk = (txt, fn) => { const b = d.createElement("button"); b.textContent = txt; b.onclick = fn; return b; };
  const modeBtn = mk(label(), () => { pipMode = pipMode === "both" ? "dst" : pipMode === "dst" ? "src" : "both"; modeBtn.textContent = label(); updatePipCaption(); });
  bar.append(modeBtn,
    mk("A-", () => { pipFont = Math.max(14, pipFont - 2); updatePipCaption(); }),
    mk("A+", () => { pipFont = Math.min(48, pipFont + 2); updatePipCaption(); }));
  const cap = d.createElement("div"); cap.id = "cap";
  d.body.append(bar, cap);
  pipWin.addEventListener("pagehide", () => { pipWin = null; });
  updatePipCaption();
}
function playPCM24k(b64) {
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const int16 = new Int16Array(bytes.buffer), f32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
  const buf = playCtx.createBuffer(1, f32.length, 24000);
  buf.getChannelData(0).set(f32);
  const node = playCtx.createBufferSource(); node.buffer = buf; node.connect(playCtx.destination);
  if (playTime < playCtx.currentTime) playTime = playCtx.currentTime;
  node.start(playTime); playTime += buf.duration;
}

// Google realtime-voice key now lives on the Interface Management page.
async function refreshGoogleKeyPlaceholder() {
  if (BOOT.server_mode || !$("iface-google-key")) return;
  try {
    const st = await api("/api/apikey?model=" + encodeURIComponent("(Google) Live Translate"));
    $("iface-google-key").value = "";
    $("iface-google-key").placeholder = st.has_key ? "已设置（留空则不修改）" : "AQ... / AIza...";
  } catch {}
}
if ($("iface-google-key")) $("iface-google-key").onchange = async () => {
  const key = $("iface-google-key").value;
  if (!key) return;
  await api("/api/apikey", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "(Google) Live Translate", api_key: key }) });
  $("iface-google-key").value = "";
  $("iface-google-key").placeholder = "已设置（留空则不修改）";
  updateLiveHint && updateLiveHint();
};

// ----- history -----
// Compact rows (status / file / type / time); clicking a row expands a detail
// panel with the full record + actions: Download (output), Continue (interrupted
// runs only), Delete (record + all data). Interrupted runs (failed/stopped) are
// shown and resumable via continue_mode — the web equivalent of the Qt page.
let historyTypesFilled = false;
const HSTATUS = {
  success: ["Status Success", "#2e7d32"],
  failed: ["Status Failed", "#c62828"],
  stopped: ["Status Stopped", "#ef6c00"],
  interrupted: ["Status Interrupted", "#ef6c00"],
  running: ["Status Running", "#1565c0"],
  paused: ["Status Paused", "#8e8e93"],
};

function _fmtDuration(sec) {
  sec = parseInt(sec || 0, 10) || 0;
  if (sec < 60) return sec + "s";
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  return `${h}h${mm ? " " + mm + "m" : ""}`;
}

function _histResumable(r) {
  if (!(r.status === "failed" || r.status === "stopped" || r.status === "interrupted")) return false;
  try { return !!JSON.parse(r.resume_info || "{}").input_file_path; }
  catch (e) { return false; }
}

// --- batch grouping + pretty (card-block) detail ---
function _aggStatus(recs) {
  const s = new Set(recs.map((r) => r.status || ""));
  if (s.has("running") || s.has("queued")) return "running";
  if (s.has("paused")) return "paused";
  if (recs.every((r) => r.status === "success")) return "success";
  for (const k of ["failed", "stopped", "interrupted"]) if (s.has(k)) return k;
  return recs[0] ? recs[0].status : "";
}
function _groupBatches(records) {
  const order = [], by = {};
  for (const r of records) {
    const bid = r.batch_id || ("__solo__" + r.id);
    if (!by[bid]) { by[bid] = []; order.push(bid); }
    by[bid].push(r);
  }
  return order.map((k) => by[k]);
}
function _fmtTok(n) { n = n || 0; return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n); }
function _statusPillEl(status) {
  const [key, color] = HSTATUS[status] || [null, null];
  const s = document.createElement("span"); s.className = "hist-pill";
  s.textContent = key ? _label(key, status) : (status || "");
  s.style.background = color || "#888";
  return s;
}
function _statChips(pairs) {
  const g = document.createElement("div"); g.className = "hist-stats";
  for (const [k, v] of pairs) {
    const c = document.createElement("div"); c.className = "hist-chip";
    const lk = document.createElement("div"); lk.className = "hist-chip-k"; lk.textContent = k;
    const lv = document.createElement("div"); lv.className = "hist-chip-v"; lv.textContent = v;
    c.append(lk, lv); g.appendChild(c);
  }
  return g;
}
function _histActBtn(acts, text, fn, cls) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "hist-act" + (cls ? " " + cls : "");
  b.textContent = text;
  b.onclick = (e) => { e.stopPropagation(); fn(); };
  acts.appendChild(b);
}

function buildHistoryDetail(r) {
  const box = document.createElement("div"); box.className = "hist-detail-box";
  const cost = (r.cost_amount != null && r.cost_currency) ? `${r.cost_amount} ${r.cost_currency}` : "—";
  box.appendChild(_statChips([
    [_label("Source Language", "语言"), `${r.src_lang_display || r.src_lang || ""} → ${r.dst_lang_display || r.dst_lang || ""}`],
    [_label("Model", "模型"), `${r.model || ""} (${r.use_online ? "Online" : "Offline"})`],
    [_label("Tokens", "Tokens"), _fmtTok(r.total_tokens)],
    [_label("Estimated cost", "费用"), cost],
    [_label("Duration", "用时"), _fmtDuration(r.duration_seconds)],
  ]));
  if (r.error_reason) {
    const e = document.createElement("div"); e.className = "hist-err";
    e.textContent = "⚠ " + r.error_reason; box.appendChild(e);
  }
  const acts = document.createElement("div"); acts.className = "hist-detail-acts";
  if (r.output_file_path) {
    _histActBtn(acts, _label("Download", "下载"),
      () => window.open("/api/history/download?id=" + encodeURIComponent(r.id), "_blank"));
  }
  if (_histResumable(r)) _histActBtn(acts, _label("Continue Translation", "继续翻译"), () => resumeHistory(r.id), "primary");
  _histActBtn(acts, _label("Delete Record", "删除"), () => deleteHistory(r.id), "danger");
  box.appendChild(acts);
  return box;
}

function buildBatchDetail(recs) {
  const box = document.createElement("div"); box.className = "hist-detail-box";
  const done = recs.filter((r) => r.status === "success").length;
  const tokens = recs.reduce((a, r) => a + (r.total_tokens || 0), 0);
  const costAmt = recs.reduce((a, r) => a + (r.cost_amount || 0), 0);
  const ccy = (recs.find((r) => r.cost_currency) || {}).cost_currency || "";
  box.appendChild(_statChips([
    [_label("Files", "文件"), `${done}/${recs.length}`],
    [_label("Source Language", "语言"), `${recs[0].src_lang_display || ""} → ${recs[0].dst_lang_display || ""}`],
    [_label("Model", "模型"), recs[0].model || ""],
    [_label("Tokens", "Tokens"), _fmtTok(tokens)],
    [_label("Estimated cost", "费用"), costAmt ? `${costAmt.toFixed(4)} ${ccy}` : "—"],
  ]));
  const list = document.createElement("div"); list.className = "hist-files";
  for (const r of recs) {
    const row = document.createElement("div"); row.className = "hist-file-row";
    const nm = document.createElement("span"); nm.className = "hist-file-name"; nm.textContent = r.input_file || "";
    row.appendChild(nm);
    row.appendChild(_statusPillEl(r.status));
    if (r.output_file_path) {
      const d = document.createElement("button"); d.type = "button"; d.className = "hist-act";
      d.textContent = _label("Download", "下载");
      d.onclick = (e) => { e.stopPropagation(); window.open("/api/history/download?id=" + encodeURIComponent(r.id), "_blank"); };
      row.appendChild(d);
    }
    if (_histResumable(r)) {
      const b = document.createElement("button"); b.type = "button"; b.className = "hist-act primary";
      b.textContent = _label("Continue Translation", "继续翻译");
      b.onclick = (e) => { e.stopPropagation(); resumeHistory(r.id); };
      row.appendChild(b);
    }
    list.appendChild(row);
  }
  box.appendChild(list);
  const acts = document.createElement("div"); acts.className = "hist-detail-acts";
  if (recs.some(_histResumable)) {
    _histActBtn(acts, _label("Continue All", "全部继续"), () => resumeBatch(recs), "primary");
  }
  _histActBtn(acts, _label("Delete Record", "删除"), () => deleteBatch(recs), "danger");
  box.appendChild(acts);
  return box;
}

async function deleteBatch(recs) {
  if (!confirm(_label("Delete Record Confirm", "删除该记录及其全部数据？"))) return;
  for (const r of recs) {
    try {
      await api("/api/history/delete", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: r.id }),
      });
    } catch (e) { /* keep deleting the rest */ }
  }
  loadHistory();
}

async function loadHistory() {
  const t = $("history-table");
  tableSkeleton(t, 4);
  const ftype = $("history-type").value;
  const fstatus = $("history-status") ? $("history-status").value : "";
  const [sortBy, descFlag] = ($("history-sort").value || "start_time|1").split("|");
  let data;
  try {
    data = await api(`/api/history?file_type=${encodeURIComponent(ftype)}&status=${encodeURIComponent(fstatus)}&sort_by=${sortBy}&desc=${descFlag === "1"}`);
  }
  catch (e) { t.innerHTML = "<tr><td style='border:none'>" + emptyState(EICON.inbox, "无法加载记录", e.message) + "</td></tr>"; return; }
  // Populate the file-type filter once (from all types present).
  if (!historyTypesFilled && data.file_types) {
    for (const ft of data.file_types) {
      const o = document.createElement("option"); o.value = ft; o.textContent = ft.toUpperCase(); $("history-type").appendChild(o);
    }
    historyTypesFilled = true;
  }
  if (!data.records.length) {
    t.innerHTML = "<tr><td style='border:none'>" +
      emptyState(EICON.inbox, "还没有翻译记录", "完成一次翻译后，项目会按文件类型与时间显示在这里。") + "</td></tr>";
    return;
  }
  t.innerHTML = `<tr><th>${_label("Status", "状态")}</th><th>${_label("Upload File", "文件")}</th><th>${_label("File Type", "类型")}</th><th>${_label("Time", "时间")}</th></tr>`;
  // One run = one batch (shared batch_id): fold its files into one parent row.
  for (const recs of _groupBatches(data.records)) {
    const single = recs.length === 1;
    const agg = _aggStatus(recs);
    const tr = document.createElement("tr");
    tr.className = "hist-row";
    const [key, color] = HSTATUS[agg] || [null, null];
    const st = document.createElement("td");
    st.textContent = key ? _label(key, agg) : (agg || "");
    if (color) st.style.color = color;
    const f = document.createElement("td");
    f.textContent = single ? (recs[0].input_file || "")
      : _label("Files Count", "{n} 个文件").replace("{n}", recs.length);
    const ty = document.createElement("td");
    if (single) { ty.textContent = (recs[0].file_type || "").toUpperCase(); }
    else { const ts = new Set(recs.map((r) => (r.file_type || "").toUpperCase())); ty.textContent = ts.size === 1 ? [...ts][0] : "—"; }
    const tm = document.createElement("td"); tm.textContent = (recs[0].start_time || "").replace("T", " ").slice(0, 16);
    tr.append(st, f, ty, tm);

    const det = document.createElement("tr"); det.className = "hist-detail"; det.hidden = true;
    const dtd = document.createElement("td"); dtd.colSpan = 4;
    dtd.appendChild(single ? buildHistoryDetail(recs[0]) : buildBatchDetail(recs));
    det.appendChild(dtd);
    tr.onclick = () => {
      const opening = det.hidden;
      // Accordion: collapse any other open detail so only one shows at a time.
      t.querySelectorAll("tr.hist-detail").forEach((d) => { if (d !== det) d.hidden = true; });
      t.querySelectorAll("tr.hist-row.expanded").forEach((r) => { if (r !== tr) r.classList.remove("expanded"); });
      det.hidden = !opening;
      tr.classList.toggle("expanded", opening);
    };
    t.appendChild(tr); t.appendChild(det);
  }
}

async function resumeHistory(id) {
  try {
    const lang = localStorage.getItem("lh-lang") || "zh";
    const { task_id } = await api("/api/history/resume", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, ui_lang: lang }),
    });
    // Jump back to the translate panel's dashboard (Qt parity: continuing a
    // stopped run returns to the same progress view).
    const tab = document.querySelector('.tab[data-tab="translate"]');
    if (tab) tab.click();
    if (typeof showTranslateSub === "function") showTranslateSub("doc");
    currentTask = task_id; setRunState("running");
    if ($("result")) $("result").hidden = true;
    setStatus(_label("Resuming Translation", "正在继续翻译") + "…");
    listenProgress(task_id);
  } catch (e) { toast(e.message || "Error", "error"); }
}

async function resumeBatch(recs) {
  // Continue EVERY unfinished file in this batch as ONE task (server loops them
  // sequentially). Per-file "继续翻译" only resumes that single file; this picks
  // up all the failed/stopped/interrupted ones at once.
  const ids = recs.filter(_histResumable).map((r) => r.id);
  if (!ids.length) return;
  try {
    const lang = localStorage.getItem("lh-lang") || "zh";
    const { task_id } = await api("/api/history/resume-batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, ui_lang: lang }),
    });
    const tab = document.querySelector('.tab[data-tab="translate"]');
    if (tab) tab.click();
    if (typeof showTranslateSub === "function") showTranslateSub("doc");
    currentTask = task_id; setRunState("running");
    if ($("result")) $("result").hidden = true;
    setStatus(_label("Resuming Translation", "正在继续翻译") + "…");
    listenProgress(task_id);
  } catch (e) { toast(e.message || "Error", "error"); }
}

async function deleteHistory(id) {
  if (!confirm(_label("Delete Record Confirm", "删除该记录及其全部数据？"))) return;
  try {
    await api("/api/history/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    loadHistory();
  } catch (e) { toast(e.message || "Error", "error"); }
}
$("history-refresh").onclick = loadHistory;
$("history-type").onchange = loadHistory;
if ($("history-status")) $("history-status").onchange = loadHistory;
$("history-sort").onchange = loadHistory;

// ----- quick translate (Google-Translate-style text box) -----
// Source = "auto" (auto-detect) + the same display-name language list the
// translate page uses; target = a concrete language. The backend takes display
// names (or "auto") directly, so no code mapping is needed here.
function initQuick() {
  const src = $("quick-src"), dst = $("quick-dst");
  if (!src || !dst) return;
  // Auto option first, then the language display names. The Auto label is
  // localized via applyI18n (it carries data-i18n="Auto Detect").
  src.innerHTML = "";
  const auto = document.createElement("option");
  auto.value = "auto"; auto.textContent = "自动识别语言"; auto.dataset.i18n = "Auto Detect";
  src.appendChild(auto);
  for (const name of BOOT.languages) {
    const o = document.createElement("option"); o.value = name; o.textContent = name; src.appendChild(o);
  }
  src.value = "auto";
  fillSelect(dst, BOOT.languages, BOOT.config.default_dst_lang || "中文");

  $("quick-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); quickTranslate(); }
  });
  $("quick-swap").onclick = quickSwap;
  $("quick-copy").onclick = quickCopy;
  $("quick-clear").onclick = quickClearHistory;
  $("quick-mic").onclick = quickMic;
  $("quick-speak").onclick = quickSpeak;

  // Voice (mic input + read-aloud) is only usable when the 翻译语音输入 plugin
  // is installed. When it isn't, keep the buttons clickable but visibly dimmed
  // with a tooltip; a click jumps to the Plugins page (the handlers redirect).
  if (!BOOT.quick_voice_available) {
    const hint = _label("Voice Needs Plugin", "语音 / 朗读需要安装语音输入插件。");
    for (const id of ["quick-mic", "quick-speak"]) {
      const b = $(id);
      if (!b) continue;
      // Drop data-i18n-title so applyI18n (runs later) won't overwrite the
      // gating tooltip with the generic action label.
      delete b.dataset.i18nTitle;
      b.title = hint;
      b.classList.add("quick-mic-off");
    }
  }
}

function onQuickShow() {
  loadQuickHistory();
  const inp = $("quick-input");
  if (inp) inp.focus();
}

async function quickTranslate() {
  const text = $("quick-input").value.trim();
  if (!text) return;
  // The active online interface needs an API key — same gate as document
  // translation (was missing here, so it translated then failed generically).
  if (useOnline() && !BOOT.server_mode) {
    const m = $("model") ? $("model").value : "";
    try {
      const st = await api("/api/apikey?model=" + encodeURIComponent(m));
      if (!st.has_key) {
        $("quick-status").textContent = _apiKeyMissingMsg(m);
        return;
      }
    } catch (e) { /* if the check itself fails, let the translate attempt surface it */ }
  }
  $("quick-status").textContent = _label("Translate", "翻译") + "…";
  try {
    const r = await api("/api/quick-translate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, src_lang: $("quick-src").value, dst_lang: $("quick-dst").value, context: ($("quick-context") ? $("quick-context").value.trim() : "") }) });
    $("quick-output").textContent = r.translated || "";
    $("quick-status").textContent = "";
    renderQuickHistory(r.history || []);
  } catch (e) {
    $("quick-status").textContent = "错误: " + (e.message || "").slice(0, 120);
  }
}

function quickSwap() {
  const src = $("quick-src"), dst = $("quick-dst");
  // If source is auto, there's no concrete language to move to the target;
  // adopt the current target as the new source instead and keep target.
  if (src.value === "auto") {
    if ([...src.options].some((o) => o.value === dst.value)) src.value = dst.value;
    else return;
  } else {
    const s = src.value; src.value = dst.value; dst.value = s;
  }
  if ($("quick-output").textContent.trim()) quickTranslate();
}

async function quickCopy() {
  const text = $("quick-output").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const btn = $("quick-copy");
    const prev = btn.textContent;
    btn.textContent = _label("Copied", "已复制");
    setTimeout(() => { btn.textContent = prev; }, 1200);
  } catch (e) { $("quick-status").textContent = "复制失败"; }
}

async function loadQuickHistory() {
  try {
    const d = await api("/api/quick-history");
    renderQuickHistory(d.history || []);
  } catch (e) { renderQuickHistory([]); }
}

// Build one clickable history row (XSS-safe: textContent only). Clicking it
// reloads the entry (langs + source + translation).
function quickHistoryRow(it) {
  const row = document.createElement("button");
  row.type = "button"; row.className = "quick-history-item";
  const s = document.createElement("span"); s.className = "qh-src"; s.textContent = it.src || "";
  const a = document.createElement("span"); a.className = "qh-arrow"; a.textContent = "→";
  const d = document.createElement("span"); d.className = "qh-dst"; d.textContent = it.translated || "";
  row.append(s, a, d);
  row.onclick = () => {
    if (it.src_lang) $("quick-src").value =
      [...$("quick-src").options].some((o) => o.value === it.src_lang) ? it.src_lang : "auto";
    if (it.dst_lang && [...$("quick-dst").options].some((o) => o.value === it.dst_lang)) $("quick-dst").value = it.dst_lang;
    $("quick-input").value = it.src || "";
    $("quick-output").textContent = it.translated || "";
  };
  return row;
}

// Collapsed by default: the <summary> shows only the most-recent entry (or a
// dash when empty); expanding reveals the full list (up to 50) + the clear
// button. Clicking a row inside the summary must NOT toggle the <details>.
function renderQuickHistory(items) {
  const recent = $("quick-history-recent");
  const box = $("quick-history-list");
  if (!recent || !box) return;

  recent.replaceChildren();
  if (!items.length) {
    const e = document.createElement("span");
    e.className = "quick-history-empty";
    e.textContent = "—";
    recent.appendChild(e);
  } else {
    const top = quickHistoryRow(items[0]);
    // The newest row lives inside the <summary>; stop its click from toggling.
    top.addEventListener("click", (ev) => ev.stopPropagation());
    recent.appendChild(top);
  }

  box.replaceChildren();
  if (!items.length) {
    const e = document.createElement("div");
    e.className = "quick-history-empty";
    e.textContent = _label("History", "历史记录") + " —";
    box.appendChild(e);
    return;
  }
  for (const it of items) box.appendChild(quickHistoryRow(it));
}

async function quickClearHistory() {
  try { const d = await api("/api/quick-history/clear", { method: "POST" }); renderQuickHistory(d.history || []); }
  catch (e) { /* server mode / unavailable */ }
}

// Voice input: reuse the live page's local capture path (VAD worklet -> one
// utterance -> POST base64 PCM16 to /api/live-recognize). One press records one
// utterance; the recognized source text is dropped into the input + translated.
let _quickRecCtx = null, _quickRecStream = null, _quickRecSrc = null, _quickRecNode = null, _quickRecording = false;
async function quickMic() {
  if (!BOOT.quick_voice_available) {
    const t = document.querySelector('.tab[data-tab="modules"]'); if (t) t.click();
    return;
  }
  if (_quickRecording) { stopQuickMic(); return; }
  try {
    _quickRecStream = await navigator.mediaDevices.getUserMedia(
      { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) { $("quick-status").textContent = "无法访问麦克风：" + e.message; return; }
  $("quick-status").textContent = "正在加载本地模型…";
  // Quick voice uses its own STT model (quick_stt_model) — preload THAT, not live.
  showModelLoading("正在加载语音模型…\n首次使用需下载并载入，请稍候");
  try { await api("/api/live-preload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "quick" }) }); }
  catch (e) { /* lazy load */ }
  finally { hideModelLoading(); }
  _quickRecCtx = new AudioContext();
  _quickRecSrc = _quickRecCtx.createMediaStreamSource(_quickRecStream);
  try {
    await _quickRecCtx.audioWorklet.addModule("/static/vad-worklet.js?v=20260616A");
    _quickRecNode = new AudioWorkletNode(_quickRecCtx, "vad-processor",
      { processorOptions: { prerollMs: 500, onMs: 90, hangMs: 900, minSegMs: 280, maxSegMs: 30000,
                            onAbs: 0.006, offAbs: 0.004 } });
    _quickRecNode.port.onmessage = onQuickVad;
    _quickRecNode.port.postMessage({ type: "mode", mode: "open" });
    _quickRecSrc.connect(_quickRecNode); _quickRecNode.connect(_quickRecCtx.destination);
  } catch (e) { $("quick-status").textContent = "VAD 初始化失败：" + e.message; stopQuickMic(); return; }
  _quickRecording = true;
  $("quick-mic").classList.add("recording");
  $("quick-status").textContent = "正在聆听…（说完一句自动识别）";
}
function stopQuickMic() {
  try {
    if (_quickRecNode) { if (_quickRecNode.port) _quickRecNode.port.postMessage({ type: "mode", mode: "block" }); _quickRecNode.disconnect(); }
    if (_quickRecSrc) _quickRecSrc.disconnect();
  } catch (e) { /* */ }
  if (_quickRecStream) _quickRecStream.getTracks().forEach((t) => t.stop());
  if (_quickRecCtx) _quickRecCtx.close();
  _quickRecNode = null; _quickRecording = false;
  $("quick-mic").classList.remove("recording");
}
function onQuickVad(e) {
  const m = e.data || {};
  if (m.type === "segment") {
    stopQuickMic();   // one utterance per press
    quickRecognize(downsamplePCM16(new Float32Array(m.pcm), m.sampleRate));
  }
}
async function quickRecognize(int16) {
  $("quick-status").textContent = "识别中…";
  try {
    // Use the quick plugin's own STT endpoint (not /api/live-recognize).
    const r = await api("/api/quick-recognize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_b64: int16ToB64(int16) }) });
    if (!r.source) { $("quick-status").textContent = "未识别到语音"; return; }
    $("quick-input").value = r.source;
    if (r.detected && [...$("quick-src").options].some((o) => o.value === r.detected)) $("quick-src").value = r.detected;
    $("quick-status").textContent = "";
    quickTranslate();
  } catch (e) { $("quick-status").textContent = "识别失败：" + (e.message || "").slice(0, 120); }
}

// Read-aloud: POST the current output text + target language to /api/tts, which
// returns an MP3 (audio/mpeg). Gated on the same plugin as the mic.
let _quickAudio = null;
async function quickSpeak() {
  if (!BOOT.quick_voice_available) {
    const t = document.querySelector('.tab[data-tab="modules"]'); if (t) t.click();
    return;
  }
  const text = $("quick-output").textContent;
  if (!text.trim()) return;
  $("quick-status").textContent = _label("Read Aloud", "朗读") + "…";
  try {
    const res = await fetch("/api/tts", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, lang: $("quick-dst").value }) });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const blob = await res.blob();
    if (_quickAudio) { try { _quickAudio.pause(); URL.revokeObjectURL(_quickAudio.src); } catch (e) {} }
    _quickAudio = new Audio(URL.createObjectURL(blob));
    _quickAudio.play();
    $("quick-status").textContent = "";
  } catch (e) { $("quick-status").textContent = "朗读失败：" + (e.message || "").slice(0, 120); }
}

boot().catch((e) => {
  const pre = document.createElement("pre");
  pre.style.padding = "24px";
  pre.textContent = "启动失败: " + (e && e.message ? e.message : e);
  document.body.innerHTML = "";
  document.body.appendChild(pre);
});
