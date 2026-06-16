// LinguaHaru Web frontend — talks to the FastAPI backend.
const $ = (id) => document.getElementById(id);

// Inline 1px-stroke icons used by JS-generated markup (no emoji).
const ICON = {
  sun:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>',
  check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 6.5"/></svg>',
  cross:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  chevron:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
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

// Online vs offline is decided by the ACTIVE interface (set in Interface
// Management), not a Settings checkbox. activateIface keeps this in sync.
function useOnline() {
  return !!(BOOT.config && BOOT.config.default_online);
}

// Model Management: STT model picker (synced with the translate page via config).
if ($("models-stt")) $("models-stt").onchange = () => {
  saveConfig({ stt_model: $("models-stt").value });
  if ($("stt-model")) $("stt-model").value = $("models-stt").value;
};
// Model Management: image-OCR model picker.
if ($("models-ocr")) $("models-ocr").onchange = () => saveConfig({ ocr_model_size: $("models-ocr").value });
// Model Management: show the unified download location + downloaded models.
async function refreshModels() {
  if ($("models-stt") && BOOT) fillSelect($("models-stt"), BOOT.stt_models, BOOT.config.stt_model);
  if ($("models-ocr") && BOOT && BOOT.ocr_models) fillSelect($("models-ocr"), BOOT.ocr_models, BOOT.config.ocr_model_size);
  if (!$("models-dir")) return;
  try {
    const d = await api("/api/models");
    $("models-dir").value = d.dir || "";
    const list = $("models-list");
    list.replaceChildren();
    if (!d.models || !d.models.length) {
      list.textContent = "尚未下载任何模型";
    } else {
      // textContent (not innerHTML): labels derive from folder names on disk.
      d.models.forEach((m, i) => {
        if (i) list.appendChild(document.createElement("br"));
        const span = document.createElement("span");
        span.textContent = `• ${m.label} — ${m.size}`;
        list.appendChild(span);
      });
    }
  } catch (e) { /* server_mode or not available */ }
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
  fillSelect($("stt-model"), BOOT.stt_models, c.stt_model);
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
  if ($("set-bi-bold")) $("set-bi-bold").checked = c.bilingual_bold !== false;
  if ($("set-bi-color")) $("set-bi-color").value = c.bilingual_color || "";
  if ($("set-live-stream")) $("set-live-stream").checked = !!c.live_stream_translation;
  if ($("set-web-vad")) $("set-web-vad").value = c.web_vad || "energy";
  if ($("set-result-dir")) $("set-result-dir").value = c.result_dir || "";
  if ($("set-hist-max")) $("set-hist-max").value = (c.history_max_records ?? 1000);
  if ($("set-hist-age")) $("set-hist-age").value = (c.history_max_age_days ?? 0);
  // PDF options (Translate page; shown only when a PDF is selected)
  if ($("pdf-translate-table")) $("pdf-translate-table").checked = !!c.pdf_translate_table;
  if ($("pdf-ocr-scanned")) $("pdf-ocr-scanned").checked = !!c.pdf_ocr_scanned;
  if ($("pdf-dual-alternating")) $("pdf-dual-alternating").checked = !!c.pdf_dual_alternating;
  if ($("pdf-pages")) $("pdf-pages").value = c.pdf_pages || "";
  if ($("pdf-only-translated")) $("pdf-only-translated").checked = !!c.pdf_only_translated_pages;
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
  if (anyMedia) applySenseVoiceRestriction();
  const anyPdf = list.some((f) => f.name.split(".").pop().toLowerCase() === "pdf");
  $("pdf-options").hidden = !anyPdf;
}

// ----- translate -----
$("translate-btn").onclick = async () => {
  if (!currentFiles.length) { setStatus("请先选择文件。"); return; }
  const online = useOnline();
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
  fd.append("ui_lang", _uiLang);

  try {
    const { task_id } = await api("/api/translate", { method: "POST", body: fd });
    currentTask = task_id;
    listenProgress(task_id);
  } catch (e) { setStatus("错误: " + e.message); setBusy(false); }
};

$("stop-btn").onclick = async () => {
  if (currentTask) await api("/api/stop/" + currentTask, { method: "POST" });
};

let _progressES = null;
function listenProgress(taskId) {
  $("progress-wrap").hidden = false;
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
    $("m-speed").textContent = m(/([\d.]+)\s*lines\/min/i);
    $("m-tokens").textContent = (desc.match(/([\d.]+\s*[KMkm]?)\s*tokens/i) || [, "—"])[1].replace(/\s/g, "");
    $("m-eta").textContent = m(/ETA\s+([\d:]+)/i);
    $("m-threads").textContent = m(/(\d+)\s*threads/i);
    if (d.status === "done") {
      es.close(); setBusy(false);
      $("download-link").href = "/api/download/" + taskId;
      $("result").hidden = false; setStatus("翻译完成");
      renderCoverage(d.coverage);
      showThanks(d.tokens, d.cost);
    } else if (d.status === "error") {
      es.close(); setBusy(false); setStatus("错误: " + (d.error || "未知错误"));
    } else if (d.status === "stopped") {
      es.close(); setBusy(false); setStatus("已停止");
    }
  };
  es.onerror = () => { es.close(); setBusy(false); };
}

function _label(key, fallback) {
  const lang = localStorage.getItem("lh-lang") || "zh";
  const L = (BOOT.labels && BOOT.labels[lang]) || {};
  const EN = (BOOT.labels && BOOT.labels.en) || {};
  return L[key] || EN[key] || fallback;
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
  $("coverage-body").textContent = parts.join(" · ");
  box.hidden = false;
}

function setBusy(b) {
  $("translate-btn").disabled = b;
  $("stop-btn").disabled = !b;
}
function setStatus(t) { $("status").textContent = t; }

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
  const t = $("modules-table");
  t.innerHTML = "";
  const head = document.createElement("tr");
  head.innerHTML = "<th>模块</th><th>模型</th><th>状态</th><th>操作</th>";
  t.appendChild(head);
  for (const m of BOOT.modules) {
    const tr = document.createElement("tr");

    // Name + short engine subtitle, stacked.
    const nameTd = document.createElement("td");
    nameTd.className = "plugin-name-cell";
    const nm = document.createElement("div"); nm.className = "plugin-name"; nm.textContent = m.name;
    const sub = document.createElement("div"); sub.className = "plugin-sub"; sub.textContent = _engineSubtitle(m.detail);
    nameTd.append(nm, sub);

    // Current model: a compact clickable chip that opens the picker modal.
    // Plugins without models (PDF) show a muted dash.
    const modelTd = document.createElement("td");
    if (m.models && m.models.length) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "model-chip";
      const txt = document.createElement("span"); txt.textContent = _currentModelLabel(m);
      const aff = document.createElement("span"); aff.className = "model-chip-aff"; aff.innerHTML = ICON.chevron;
      chip.append(txt, aff);
      chip.onclick = () => openPluginModelModal(m, chip, txt);
      modelTd.appendChild(chip);
    } else {
      modelTd.className = "plugin-sub"; modelTd.textContent = "—";
    }

    const statTd = document.createElement("td"); statTd.innerHTML = m.available ? pill("on", "已安装", ICON.check) : pill("off", "未安装", ICON.cross);

    const actTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = m.available ? "卸载" : "安装";
    btn.onclick = () => moduleAction(m.name, m.available ? "uninstall" : "install", btn, statTd);
    actTd.appendChild(btn);

    tr.append(nameTd, modelTd, statTd, actTd);
    t.appendChild(tr);
    if (m.available) checkModuleUpdate(m.name, actTd, statTd);
  }
}

// Open the model picker modal for a plugin: a radio list of available models
// (current one preselected, each with its size/VRAM info as a muted hint).
// On "切换" the chosen model is persisted + downloaded/warmed (polled), then
// the plugin's chip text is updated and the modal closes.
function openPluginModelModal(m, chip, chipText) {
  $("plugin-model-title").textContent = m.name;
  $("plugin-model-status").textContent = "";
  const list = $("plugin-model-list");
  list.innerHTML = "";
  for (const mdl of m.models) {
    const row = document.createElement("label");
    row.className = "model-radio";
    const radio = document.createElement("input");
    radio.type = "radio"; radio.name = "plugin-model-pick"; radio.value = mdl.id;
    if (mdl.id === m.current_model) radio.checked = true;
    const body = document.createElement("span"); body.className = "model-radio-body";
    const lbl = document.createElement("span"); lbl.className = "model-radio-label"; lbl.textContent = mdl.label;
    body.appendChild(lbl);
    if (mdl.info) {
      const info = document.createElement("span"); info.className = "model-radio-info"; info.textContent = mdl.info;
      body.appendChild(info);
    }
    row.append(radio, body);
    list.appendChild(row);
  }
  const modal = $("plugin-model-modal");
  const switchBtn = $("plugin-model-switch");
  switchBtn.disabled = false;
  const close = () => {
    modal.hidden = true;
    switchBtn.onclick = null; $("plugin-model-cancel").onclick = null; modal.onclick = null;
  };
  $("plugin-model-cancel").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };
  switchBtn.onclick = () => {
    const picked = list.querySelector('input[name="plugin-model-pick"]:checked');
    if (!picked) return;
    const modelId = picked.value;
    if (modelId === m.current_model) { close(); return; }
    switchPluginModel(m, modelId, { chip, chipText, switchBtn, close });
  };
  modal.hidden = false;
}

async function switchPluginModel(m, modelId, ui) {
  const status = $("plugin-model-status");
  ui.switchBtn.disabled = true;
  status.innerHTML = pill("busy", _label("Downloading Model", "正在下载模型…"), "");
  try {
    await api("/api/modules/model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: m.name, model_id: modelId }),
    });
  } catch (e) {
    ui.switchBtn.disabled = false;
    status.innerHTML = pill("bad", "失败", ICON.cross) + " " + (e.message || "").slice(-200);
    return;
  }
  const poll = setInterval(async () => {
    const s = await api("/api/modules/status?name=" + encodeURIComponent(m.name));
    if (s.status === "running") return;
    clearInterval(poll);
    if (s.status === "done") {
      status.innerHTML = pill("on", _label("Model Ready", "模型就绪"), ICON.check);
      m.current_model = modelId;
      const chosen = m.models.find((x) => x.id === modelId);
      ui.chipText.textContent = chosen ? chosen.label : modelId;
      setTimeout(ui.close, 600);
    } else {
      ui.switchBtn.disabled = false;
      status.innerHTML = pill("bad", "失败", ICON.cross) + " " + (s.output || "").slice(-200);
    }
  }, 1500);
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
      let msg = `${name} ${verb}完成 —— 请重启程序以生效。`;
      if (action === "install") msg += " " + _label("Downloading Model", "正在下载模型…");
      $("modules-status").textContent = msg;
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
const PIP_MAX = 60;
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
  try {
    liveStream = await acquireLiveStream();
  } catch (e) { setLiveStatus("无法访问输入设备：" + e.message); return; }
  $("live-input").textContent = ""; $("live-output").textContent = "";
  // Preload the local model so the first sentence isn't blocked on a slow load.
  setLiveStatus("正在加载本地模型…（首次需下载/加载，请稍候）");
  showModelLoading("正在加载语音模型…\n首次使用需下载并载入，请稍候");
  try { await api("/api/live-preload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "live" }) }); }
  catch (e) { /* load lazily */ }
  finally { hideModelLoading(); }
  liveCtx = new AudioContext();
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
  startMicMeter();
}
async function startWorkletVad() {
  await liveCtx.audioWorklet.addModule("/static/vad-worklet.js?v=20260616A");
  liveNode = new AudioWorkletNode(liveCtx, "vad-processor",
    { processorOptions: { prerollMs: 500, onMs: 90, hangMs: 900, minSegMs: 280, maxSegMs: 30000,
                          onAbs: 0.006, offAbs: 0.004 } });
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
let liveSessionTokens = 0;   // accumulated tokens this live session (for the thanks card)
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
async function recognizeInt16(int16, final) {
  const r = await api("/api/live-recognize", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_b64: int16ToB64(int16), final: !!final }) });
  return r;
}
async function streamPartial(int16) {
  if (livePartialBusy) { livePendingPcm = int16; return; }   // latest-wins
  livePartialBusy = true;
  try {
    const r = await recognizeInt16(int16, false);
    if (r.busy) return;   // server dropped this partial under load — keep state, retry next
    liveLastDetected = r.detected || liveLastDetected;
    const text = r.source || "";
    // LocalAgreement-2: only the prefix two consecutive partials agree on is stable.
    const stable = commonPrefix(text, liveLastText);
    liveLastText = text;
    // If STT revised the already-committed region, resync length without re-emitting.
    if (!stable.startsWith(liveCommittedText)) liveCommittedText = stable.slice(0, liveCommittedText.length);
    const pending = stable.slice(liveCommittedText.length);
    const { units, consumed } = splitScored(pending, false);
    for (const u of units) commitLiveSentence(u);
    liveCommittedText += pending.slice(0, consumed);
    pipInterim = text.slice(liveCommittedText.length);      // live, not-yet-committed tail
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
    // Translate only what hasn't been committed yet (committed text is a prefix).
    // Only translate the genuinely-new tail. If the final pass CONTRACTED/revised
    // below the committed prefix, add nothing (the streamed commits already
    // covered it) — re-translating the whole text would duplicate shown lines.
    const rest = text.startsWith(liveCommittedText) ? text.slice(liveCommittedText.length) : "";
    const { units } = splitScored(rest, true);
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
  if (!source) return;
  liveCommitChain = liveCommitChain.then(() => _doCommitSentence(source));
  return liveCommitChain;
}
async function _doCommitSentence(source) {
  const ts = liveTimeStamp();
  appendLive("live-input", `[${ts}] ${source}\n`);
  const streaming = !!(BOOT.config && BOOT.config.live_stream_translation);
  const dst = $("live-target").value;
  if (!streaming) {
    try {
      const t = await api("/api/live-translate-text", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, src_lang: liveLastDetected || "auto", dst_lang: dst }) });
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
      body: JSON.stringify({ source, src_lang: liveLastDetected || "auto", dst_lang: dst }) });
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
    const tail = pipEntries[pipEntries.length - 1];
    if (tail && !tail.dst) tail.dst = last;     // pair with the source line
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
  for (const e of pipEntries) {
    const block = d.createElement("div"); block.className = "blk";
    if (pipMode !== "dst" && e.src) {
      const s = d.createElement("div"); s.className = "c-src";
      s.style.fontSize = Math.round(pipFont * 0.7) + "px";
      s.textContent = e.src; block.appendChild(s);
    }
    if (pipMode !== "src" && e.dst) {
      const t = d.createElement("div"); t.className = "c-dst";
      t.style.fontSize = pipFont + "px";
      t.textContent = e.dst; block.appendChild(t);
    }
    if (block.childNodes.length) cap.appendChild(block);
  }
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

boot().catch((e) => { document.body.innerHTML = "<pre style='padding:24px'>启动失败: " + e.message + "</pre>"; });
