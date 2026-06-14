// LinguaHaru Web frontend — talks to the FastAPI backend.
const $ = (id) => document.getElementById(id);
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
    if (t.dataset.tab === "proofread") loadProofreadDocs();
    if (t.dataset.tab === "history") loadHistory();
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
  fillLiveTarget();
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
  if (online) {
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

// ----- proofread -----
let proofreadCols = [];
async function loadProofreadDocs() {
  const data = await api("/api/proofread/docs");
  fillSelect($("proofread-select"), data.docs.length ? data.docs : ["(无可校对文档)"]);
  if (!data.docs.length) {
    $("proofread-table").replaceChildren();
    $("proofread-status").textContent = "完成一次翻译后即可在此校对（不支持 PDF）。";
    return;
  }
  // Only build the (potentially large) table the first time — re-clicking the
  // tab just refreshes the doc list, so opening 校对 stays instant.
  if ($("proofread-table").querySelectorAll("tr").length === 0) {
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

// ----- live voice translation (Gemini 3.5 Live Translate) -----
let liveWS = null, liveCtx = null, liveSrc = null, liveProc = null, liveStream = null;
let playCtx = null, playTime = 0;

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

$("live-start").onclick = async () => {
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
  liveWS.onclose = () => setLiveStatus("连接已关闭");
  liveWS.onerror = () => setLiveStatus("连接错误");

  liveProc.onaudioprocess = (e) => {
    if (!liveWS || liveWS.readyState !== 1) return;
    liveWS.send(JSON.stringify({ audio: int16ToB64(downsamplePCM16(e.inputBuffer.getChannelData(0), srcRate)) }));
  };
  liveSrc.connect(liveProc); liveProc.connect(liveCtx.destination);
  setLiveBusy(true);
};

$("live-stop").onclick = () => {
  try { if (liveProc) liveProc.disconnect(); if (liveSrc) liveSrc.disconnect(); } catch (e) { /* */ }
  if (liveStream) liveStream.getTracks().forEach((t) => t.stop());
  if (liveWS && liveWS.readyState === 1) { try { liveWS.send(JSON.stringify({ end: true })); } catch (e) {} liveWS.close(); }
  if (liveCtx) liveCtx.close();
  setLiveBusy(false); setLiveStatus("已停止");
};

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

$("set-google-key").onchange = async () => {
  const key = $("set-google-key").value;
  if (!key) return;
  await api("/api/apikey", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "(Google) Live Translate", api_key: key }) });
  $("settings-status").textContent = "Google 密钥已保存。";
};

// ----- history -----
async function loadHistory() {
  const data = await api("/api/history");
  const t = $("history-table");
  t.innerHTML = "<tr><th>文件</th><th>语言</th><th>模型</th><th>状态</th><th>Tokens</th><th>时间</th></tr>";
  for (const r of data.records) {
    const tr = document.createElement("tr");
    const cells = [
      r.input_file || "", `${r.src_lang_display || r.src_lang || ""}→${r.dst_lang_display || r.dst_lang || ""}`,
      r.model || "", r.status || "", r.total_tokens != null ? String(r.total_tokens) : "",
      (r.start_time || "").replace("T", " ").slice(0, 19)];
    for (const c of cells) { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); }
    t.appendChild(tr);
  }
  if (!data.records.length) t.innerHTML += "<tr><td colspan='6' class='muted'>暂无记录</td></tr>";
}
$("history-refresh").onclick = loadHistory;

boot().catch((e) => { document.body.innerHTML = "<pre style='padding:24px'>启动失败: " + e.message + "</pre>"; });
