// LinguaHaru Web frontend — talks to the FastAPI backend.
const $ = (id) => document.getElementById(id);

// Inline 1px-stroke icons used by JS-generated markup (no emoji).
const ICON = {
  sun:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>',
  check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 6.5"/></svg>',
  cross:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};
const pill = (cls, label, icon) => `<span class="pill ${cls}">${icon || ""}${label}</span>`;

// Empty-state + loading-skeleton helpers (no emoji; 1px line glyphs).
const EICON = {
  inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13l3-7h12l3 7"/><path d="M3 13v5a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-5"/><path d="M3 13h5l1.5 2.5h5L21 13"/></svg>',
  files: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h5l4 4v11a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4"/><path d="M5 7v12a1 1 0 0 0 1 1h8"/></svg>',
};
const emptyState = (icon, title, sub) =>
  `<div class="empty-state"><div class="es-icon">${icon}</div>` +
  `<div class="es-title">${title}</div><div class="es-sub">${sub}</div></div>`;
function tableSkeleton(t, n) {
  t.innerHTML = "";
  for (let i = 0; i < (n || 5); i++)
    t.innerHTML += '<tr><td style="border:none;padding:5px 2px"><div class="skeleton"><div class="sk"></div></div></td></tr>';
}
let BOOT = null;
let currentFiles = [];
let currentTask = null;
const MEDIA_EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"];
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
async function extractAudio(file) {
  const ff = await getFfmpeg();
  const { fetchFile } = window.FFmpegUtil;
  const inName = "in_" + file.name.replace(/[^\w.]/g, "_");
  await ff.writeFile(inName, await fetchFile(file));
  await ff.exec(["-i", inName, "-vn", "-ac", "1", "-ar", "16000", "out.wav"]);
  const data = await ff.readFile("out.wav");
  try { await ff.deleteFile(inName); await ff.deleteFile("out.wav"); } catch (e) { /* ignore */ }
  const stem = file.name.replace(/\.[^.]+$/, "");
  return new File([data.buffer], stem + ".wav", { type: "audio/wav" });
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
    card.innerHTML = `<div class="if-name">${it.name}</div><div class="if-sub mono">${it.sub || ""}</div>` +
      (it.name === _ifaceActive ? '<span class="if-badge">✓</span>' : "");
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
  if ($("set-online")) $("set-online").checked = online;
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
function applyI18n(lang) {
  const L = (BOOT.labels && BOOT.labels[lang]) || {};
  const EN = (BOOT.labels && BOOT.labels.en) || {};
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const k = el.dataset.i18n;
    el.textContent = L[k] || EN[k] || el.textContent;
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
  fillSelect($("stt-model"), BOOT.stt_models, c.stt_model);
  $("translate-subs").checked = c.translate_subtitles;

  // settings (per-model key/RPM/thread/retries now live in Interface Management)
  $("set-online").checked = c.default_online;
  $("set-lan").checked = !!c.lan_mode;
  $("set-lan-admin").placeholder = c.has_lan_admin ? "已设置（留空则不修改）" : "留空则不启用";
  $("set-auto-glossary").checked = !!c.auto_extract_glossary;
  fillSelect($("glossary-edit-select"), BOOT.glossaries, c.default_glossary);
  renderModules();
  fillLiveTarget();
  updateLiveHint();
  if (BOOT.server_mode) applyServerMode();
  refreshApiKeyState();
  refreshMediaNote();
  renderDropBg();
  initUiLang();
  checkUpdate();
}

// ----- update banner -----
async function checkUpdate() {
  try {
    const u = await api("/api/update-check");
    if (u && u.update) {
      $("update-text").textContent = `发现新版本 ${u.latest}（当前 ${u.current}）`;
      $("update-link").href = u.url;
      $("update-banner").hidden = false;
    }
  } catch (e) { /* offline / unreachable — silently skip */ }
}
$("update-dismiss").onclick = () => { $("update-banner").hidden = true; };

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
  const online = $("set-online").checked;
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
  if (anyMedia) applySenseVoiceRestriction();
}

// ----- translate -----
$("translate-btn").onclick = async () => {
  if (!currentFiles.length) { setStatus("请先选择文件。"); return; }
  const online = $("set-online").checked;
  if (online && !BOOT.server_mode) {
    const st = await api("/api/apikey?model=" + encodeURIComponent($("model").value));
    if (!st.has_key) { setStatus("尚未设置 API 密钥，请在设置中填写。"); return; }
  }
  setBusy(true);
  $("result").hidden = true; setStatus("");

  // For each video, extract the audio track in-browser to avoid uploading the
  // whole file (the result is only a subtitle file anyway).
  const fd = new FormData();
  for (const f of currentFiles) {
    let uploadFile = f;
    const ext = "." + f.name.split(".").pop().toLowerCase();
    if (VIDEO_EXTS.includes(ext)) {
      setStatus(`正在浏览器内提取音轨：${f.name}（避免上传整段视频）…`);
      try { uploadFile = await extractAudio(f); }
      catch (e) { console.warn("ffmpeg.wasm failed, uploading original:", e); uploadFile = f; }
    }
    fd.append("files", uploadFile);
  }
  fd.append("src_lang", $("src-lang").value);
  fd.append("dst_lang", $("dst-lang").value);
  fd.append("model", $("model").value);
  fd.append("use_online", online);
  fd.append("glossary", $("glossary").value);
  fd.append("bilingual", $("translate-bilingual").checked);

  try {
    const { task_id } = await api("/api/translate", { method: "POST", body: fd });
    currentTask = task_id;
    listenProgress(task_id);
  } catch (e) { setStatus("错误: " + e.message); setBusy(false); }
};

$("stop-btn").onclick = async () => {
  if (currentTask) await api("/api/stop/" + currentTask, { method: "POST" });
};

function listenProgress(taskId) {
  $("progress-wrap").hidden = false;
  const es = new EventSource("/api/progress/" + taskId);
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
    $("m-speed").textContent = m(/([\d.]+)\s*lines\/min/i);
    $("m-tokens").textContent = (desc.match(/([\d.]+\s*[KMkm]?)\s*tokens/i) || [, "—"])[1].replace(/\s/g, "");
    $("m-eta").textContent = m(/ETA\s+([\d:]+)/i);
    $("m-threads").textContent = m(/(\d+)\s*threads/i);
    if (d.status === "done") {
      es.close(); setBusy(false);
      $("download-link").href = "/api/download/" + taskId;
      $("result").hidden = false; setStatus("翻译完成");
    } else if (d.status === "error") {
      es.close(); setBusy(false); setStatus("错误: " + (d.error || "未知错误"));
    } else if (d.status === "stopped") {
      es.close(); setBusy(false); setStatus("已停止");
    }
  };
  es.onerror = () => { es.close(); setBusy(false); };
}

function setBusy(b) {
  $("translate-btn").disabled = b;
  $("stop-btn").disabled = !b;
}
function setStatus(t) { $("status").textContent = t; }

// ----- settings -----
$("set-online").onchange = () => {
  const online = $("set-online").checked;
  saveConfig({ default_online: online });
  fillSelect($("model"), modelsForMode(online), online ? BOOT.config.default_online_model : null);
  refreshApiKeyState();
};
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
// Per-model key/RPM/thread/retries moved to Interface Management; their old
// Settings controls were removed.

function renderModules() {
  const t = $("modules-table");
  t.innerHTML = "";
  const head = document.createElement("tr");
  head.innerHTML = "<th>模块</th><th>状态</th><th>引擎</th><th>操作</th>";
  t.appendChild(head);
  for (const m of BOOT.modules) {
    const tr = document.createElement("tr");
    const nameTd = document.createElement("td"); nameTd.textContent = m.name;
    const statTd = document.createElement("td"); statTd.innerHTML = m.available ? pill("on", "已安装", ICON.check) : pill("off", "未安装", ICON.cross);
    const engTd = document.createElement("td"); engTd.textContent = m.detail;
    const actTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = m.available ? "卸载" : "安装";
    btn.onclick = () => moduleAction(m.name, m.available ? "uninstall" : "install", btn, statTd);
    actTd.appendChild(btn);
    tr.append(nameTd, statTd, engTd, actTd);
    t.appendChild(tr);
    if (m.available) checkModuleUpdate(m.name, actTd, statTd);
  }
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
  btn.disabled = true;
  const verb = _MODULE_VERBS[action] || action;
  statTd.innerHTML = pill("busy", verb + "中", "");
  await api("/api/modules/" + action, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }) });
  const poll = setInterval(async () => {
    const s = await api("/api/modules/status?name=" + encodeURIComponent(name));
    if (s.status === "running") return;
    clearInterval(poll);
    btn.disabled = false;
    if (s.status === "done") {
      statTd.innerHTML = pill("on", "完成", ICON.check);
      $("modules-status").textContent = `${name} ${verb}完成 —— 请重启程序以生效。`;
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
  head.innerHTML = data.columns.map((c) => `<th>${c}</th>`).join("");
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

// ----- proofread -----
let proofreadCols = [];
async function loadProofreadDocs() {
  const data = await api("/api/proofread/docs");
  fillSelect($("proofread-select"), data.docs.length ? data.docs : ["(无可校对文档)"]);
  if (!data.docs.length) {
    $("proofread-table").innerHTML = "<tr><td style='border:none'>" +
      emptyState(EICON.files, "暂无可校对的文档", "完成一次翻译后会出现在这里（不支持 PDF）。") + "</td></tr>";
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
//  · local : client VAD (vad-worklet) -> POST /api/live-local (SenseVoice + LLM)
//  · google: stream 16k PCM over /ws/live-translate (Gemini 3.5 Live Translate)
let liveWS = null, liveCtx = null, liveSrc = null, liveProc = null, liveStream = null;
let playCtx = null, playTime = 0;
let liveMode = "local", liveNode = null, liveRunning = false;

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
}
function setLiveStatus(t) { $("live-status").textContent = t; }
function setLiveBusy(b) { $("live-start").disabled = b; $("live-stop").disabled = !b; }

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
async function updateLiveHint() {
  let msg = "";
  if (liveMode === "local") {
    if (!BOOT.local_live_available) msg = "本地模式需要「Video/Audio」插件（SenseVoice）。请前往「插件」安装。";
  } else {
    const st = await api("/api/apikey?model=" + encodeURIComponent("(Google) Live Translate")).catch(() => ({ has_key: false }));
    if (!st.has_key) msg = "Google 实时翻译需要 Google API Key。请在「设置」中填写。";
  }
  $("live-hint-text").textContent = msg;
  $("live-hint").hidden = !msg;
  return !msg;  // ready?
}

$("live-start").onclick = async () => {
  if (liveRunning) return;
  if (!(await updateLiveHint())) return;   // blocked: hint already shown
  if (liveMode === "google") startGoogle(); else startLocal();
};
$("live-stop").onclick = () => { if (liveMode === "google") stopGoogle(); else stopLocal(); };

// --- Google (Gemini Live): continuous 16k PCM over WS, plays 24k reply audio ---
async function startGoogle() {
  try {
    liveStream = await navigator.mediaDevices.getUserMedia(
      { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (e) { setLiveStatus("无法访问麦克风：" + e.message); return; }
  $("live-input").textContent = ""; $("live-output").textContent = "";
  liveCtx = new AudioContext();
  const srcRate = liveCtx.sampleRate;
  liveSrc = liveCtx.createMediaStreamSource(liveStream);
  liveProc = liveCtx.createScriptProcessor(4096, 1, 1);
  playCtx = new AudioContext({ sampleRate: 24000 }); playTime = 0;

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  liveWS = new WebSocket(`${proto}//${location.host}/ws/live-translate?target=${encodeURIComponent($("live-target").value)}`);
  liveWS.onopen = () => setLiveStatus("正在聆听…（对着麦克风说话）");
  liveWS.onmessage = onLiveMessage;
  liveWS.onclose = () => { setLiveStatus("连接已关闭"); liveRunning = false; setLiveBusy(false); };
  liveWS.onerror = () => setLiveStatus("连接错误");

  liveProc.onaudioprocess = (e) => {
    if (!liveWS || liveWS.readyState !== 1) return;
    liveWS.send(JSON.stringify({ audio: int16ToB64(downsamplePCM16(e.inputBuffer.getChannelData(0), srcRate)) }));
  };
  liveSrc.connect(liveProc); liveProc.connect(liveCtx.destination);
  liveRunning = true; setLiveBusy(true);
}
function stopGoogle() {
  try { if (liveProc) liveProc.disconnect(); if (liveSrc) liveSrc.disconnect(); } catch (e) { /* */ }
  if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
  if (liveWS && liveWS.readyState === 1) { try { liveWS.send(JSON.stringify({ end: true })); } catch (e) {} liveWS.close(); }
  if (liveCtx) liveCtx.close();
  liveRunning = false; setLiveBusy(false); setLiveStatus("已停止");
}

// --- Local (SenseVoice + LLM): audio-thread VAD segments -> POST per utterance ---
async function startLocal() {
  try {
    liveStream = await navigator.mediaDevices.getUserMedia(
      { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (e) { setLiveStatus("无法访问麦克风：" + e.message); return; }
  $("live-input").textContent = ""; $("live-output").textContent = "";
  liveCtx = new AudioContext();
  liveSrc = liveCtx.createMediaStreamSource(liveStream);
  try {
    await liveCtx.audioWorklet.addModule("/static/vad-worklet.js");
    liveNode = new AudioWorkletNode(liveCtx, "vad-processor",
      { processorOptions: { prerollMs: 500, onMs: 90, hangMs: 850, minSegMs: 280, maxSegMs: 30000 } });
    liveNode.port.onmessage = onVadMessage;
    liveNode.port.postMessage({ type: "mode", mode: "open" });
    liveSrc.connect(liveNode); liveNode.connect(liveCtx.destination);
  } catch (e) {
    setLiveStatus("VAD 初始化失败：" + e.message); stopLocal(); return;
  }
  liveRunning = true; setLiveBusy(true); setLiveStatus("正在聆听…（对着麦克风说话）");
}
function stopLocal() {
  try {
    if (liveNode) { if (liveNode.port) liveNode.port.postMessage({ type: "mode", mode: "block" }); liveNode.disconnect(); }
    if (liveSrc) liveSrc.disconnect();
  } catch (e) { /* */ }
  if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
  if (liveCtx) liveCtx.close();
  liveNode = null; liveRunning = false; setLiveBusy(false); setLiveStatus("已停止");
}
function onVadMessage(e) {
  const m = e.data || {};
  if (m.type === "speechstart") setLiveStatus("识别中…");
  else if (m.type === "segment") sendLocalUtterance(downsamplePCM16(new Float32Array(m.pcm), m.sampleRate));
}
async function sendLocalUtterance(int16) {
  try {
    const r = await api("/api/live-local", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_b64: int16ToB64(int16), dst_lang: $("live-target").value,
        model: $("model").value, use_online: $("set-online").checked }) });
    if (r.source) appendLive("live-input", r.source + "\n");
    if (r.translated) appendLive("live-output", r.translated + "\n");
    if (liveRunning) setLiveStatus("正在聆听…（对着麦克风说话）");
  } catch (e) { setLiveStatus("翻译失败：" + e.message); }
}

function downsamplePCM16(input, srcRate) {
  const ratio = srcRate / 16000, outLen = Math.floor(input.length / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)] || 0));
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
function appendLive(id, text) { const el = $(id); el.textContent += text; el.scrollTop = el.scrollHeight; }
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
let historyTypesFilled = false;
async function loadHistory() {
  const t = $("history-table");
  tableSkeleton(t, 7);
  const ftype = $("history-type").value;
  const [sortBy, descFlag] = ($("history-sort").value || "start_time|1").split("|");
  let data;
  try {
    data = await api(`/api/history?file_type=${encodeURIComponent(ftype)}&sort_by=${sortBy}&desc=${descFlag === "1"}`);
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
  t.innerHTML = "<tr><th>文件</th><th>类型</th><th>语言</th><th>模型</th><th>状态</th><th>Tokens</th><th>费用</th><th>时间</th></tr>";
  for (const r of data.records) {
    const tr = document.createElement("tr");
    const cost = (r.cost_amount != null && r.cost_currency) ? `${r.cost_amount} ${r.cost_currency}` : "";
    const cells = [
      r.input_file || "", (r.file_type || "").toUpperCase(),
      `${r.src_lang_display || r.src_lang || ""}→${r.dst_lang_display || r.dst_lang || ""}`,
      r.model || "", r.status || "", r.total_tokens != null ? String(r.total_tokens) : "",
      cost, (r.start_time || "").replace("T", " ").slice(0, 19)];
    for (const c of cells) { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); }
    t.appendChild(tr);
  }
}
$("history-refresh").onclick = loadHistory;
$("history-type").onchange = loadHistory;
$("history-sort").onchange = loadHistory;

boot().catch((e) => { document.body.innerHTML = "<pre style='padding:24px'>启动失败: " + e.message + "</pre>"; });
