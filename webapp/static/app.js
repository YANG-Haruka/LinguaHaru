// LinguaHaru Web frontend — talks to the FastAPI backend.
const $ = (id) => document.getElementById(id);
let BOOT = null;
let currentFile = null;
let currentTask = null;
const MEDIA_EXTS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"];

async function api(path, opts) {
  const r = await fetch(path, opts);
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
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.querySelector(`.panel[data-panel="${t.dataset.tab}"]`).classList.add("active");
    if (t.dataset.tab === "glossary") loadGlossaryTable($("glossary-edit-select").value);
  };
});

// ----- theme -----
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  $("theme-toggle").textContent = theme === "dark" ? "☀️" : "🌙";
  localStorage.setItem("lh-theme", theme);
}
$("theme-toggle").onclick = () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");

// ----- bootstrap -----
async function boot() {
  applyTheme(localStorage.getItem("lh-theme") || "light");
  BOOT = await api("/api/bootstrap");
  const c = BOOT.config;

  fillSelect($("src-lang"), BOOT.languages, c.default_src_lang);
  fillSelect($("dst-lang"), BOOT.languages, c.default_dst_lang);
  fillSelect($("model"), modelsForMode(c.default_online), c.default_online_model);
  fillSelect($("glossary"), BOOT.glossaries, c.default_glossary);
  fillSelect($("stt-model"), BOOT.stt_models, c.stt_model);
  $("translate-subs").checked = c.translate_subtitles;
  $("accepted").textContent = "支持: " + acceptedExts().join(" ");

  // settings
  $("set-online").checked = c.default_online;
  fillSelect($("set-model"), BOOT.online_models, c.default_online_model);
  $("set-retries").value = c.max_retries;
  fillSelect($("glossary-edit-select"), BOOT.glossaries, c.default_glossary);
  renderModules();
  refreshApiKeyState();
  refreshMediaNote();
}

function acceptedExts() {
  const core = [".docx", ".pptx", ".xlsx", ".srt", ".txt", ".md", ".epub", ".csv",
    ".tsv", ".html", ".htm", ".odt", ".json", ".vtt", ".ass", ".ssa", ".lrc"];
  const extra = [];
  for (const m of BOOT.modules) {
    if (!m.available) continue;
    if (m.name === "PDF") extra.push(".pdf");
    if (m.name === "Image OCR") extra.push(".png", ".jpg", ".jpeg", ".bmp", ".webp");
    if (m.name === "Video/Audio") extra.push(...MEDIA_EXTS);
  }
  return core.concat(extra);
}

// ----- API key state -----
async function refreshApiKeyState() {
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
  fillSelect($("src-lang"), langs, langs.includes(cur) ? cur : langs[0]);
}
function refreshMediaNote() {
  $("media-note").textContent = isSenseVoice($("stt-model").value)
    ? "SenseVoice 仅支持 中/繁/英/日/韩，源语言已自动限制。" : "";
}

async function saveConfig(obj) {
  try { await api("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj) }); }
  catch (e) { console.warn("saveConfig", e); }
}

// ----- file upload (drag/drop + click) -----
const dz = $("dropzone");
dz.onclick = () => $("file-input").click();
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
dz.ondragleave = () => dz.classList.remove("dragover");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("dragover"); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); };
$("file-input").onchange = (e) => { if (e.target.files[0]) setFile(e.target.files[0]); };

function setFile(f) {
  currentFile = f;
  $("drop-text").textContent = f.name + "  (" + (f.size / 1048576).toFixed(1) + " MB)";
  const ext = "." + f.name.split(".").pop().toLowerCase();
  const isMedia = MEDIA_EXTS.includes(ext);
  $("media-options").hidden = !isMedia;
  if (isMedia) applySenseVoiceRestriction();
}

// ----- translate -----
$("translate-btn").onclick = async () => {
  if (!currentFile) { setStatus("请先选择文件。"); return; }
  const online = $("set-online").checked;
  if (online) {
    const st = await api("/api/apikey?model=" + encodeURIComponent($("model").value));
    if (!st.has_key) { setStatus("尚未设置 API 密钥，请在设置中填写。"); return; }
  }
  const fd = new FormData();
  fd.append("file", currentFile);
  fd.append("src_lang", $("src-lang").value);
  fd.append("dst_lang", $("dst-lang").value);
  fd.append("model", $("model").value);
  fd.append("use_online", online);
  fd.append("glossary", $("glossary").value);

  setBusy(true);
  $("result").hidden = true; setStatus("");
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
    $("progress-bar").style.width = Math.round((d.progress || 0) * 100) + "%";
    $("progress-desc").textContent = d.desc || "";
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
$("set-retries").onchange = () => saveConfig({ max_retries: parseInt($("set-retries").value || "4", 10) });
$("set-model").onchange = async () => {
  const st = await api("/api/apikey?model=" + encodeURIComponent($("set-model").value));
  $("set-apikey").value = "";
  $("set-apikey").placeholder = st.has_key ? "已设置（留空则不修改）" : "在此输入您的 API 密钥";
};
$("set-apikey").onchange = async () => {
  const key = $("set-apikey").value;
  if (!key) return;
  await api("/api/apikey", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: $("set-model").value, api_key: key }) });
  $("settings-status").textContent = "API 密钥已保存。";
  refreshApiKeyState();
};

function renderModules() {
  const t = $("modules-table");
  t.innerHTML = "";
  const head = document.createElement("tr");
  head.innerHTML = "<th>模块</th><th>状态</th><th>引擎</th><th>操作</th>";
  t.appendChild(head);
  for (const m of BOOT.modules) {
    const tr = document.createElement("tr");
    const nameTd = document.createElement("td"); nameTd.textContent = m.name;
    const statTd = document.createElement("td"); statTd.textContent = m.available ? "✅" : "❌";
    const engTd = document.createElement("td"); engTd.textContent = m.detail;
    const actTd = document.createElement("td");
    const btn = document.createElement("button");
    btn.textContent = m.available ? "卸载" : "安装";
    btn.onclick = () => moduleAction(m.name, m.available ? "uninstall" : "install", btn, statTd);
    actTd.appendChild(btn);
    tr.append(nameTd, statTd, engTd, actTd);
    t.appendChild(tr);
  }
}

async function moduleAction(name, action, btn, statTd) {
  btn.disabled = true;
  statTd.textContent = action === "install" ? "安装中…" : "卸载中…";
  await api("/api/modules/" + action, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }) });
  const poll = setInterval(async () => {
    const s = await api("/api/modules/status?name=" + encodeURIComponent(name));
    if (s.status === "running") return;
    clearInterval(poll);
    btn.disabled = false;
    if (s.status === "done") {
      statTd.textContent = "✅ 完成";
      $("settings-status").textContent = `${name} ${action === "install" ? "安装" : "卸载"}完成 —— 请重启程序以生效。`;
    } else {
      statTd.textContent = "❌ 失败";
      $("settings-status").textContent = `${name} 操作失败：` + (s.output || "").slice(-300);
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

boot().catch((e) => { document.body.innerHTML = "<pre style='padding:24px'>启动失败: " + e.message + "</pre>"; });
