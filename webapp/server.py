"""LinguaHaru Web — FastAPI backend (replaces the Gradio app).

The translation backend is fully reused from core.backend (which is Qt-free,
pure-Python glue) and core.api_keys, so this layer is a thin HTTP wrapper:
  - serves the custom frontend (webapp/static)
  - exposes config / models / glossary / API-key endpoints
  - runs a translation in a background thread and streams progress over SSE

Run:  uvicorn webapp.server:app  (or python -m webapp.server)
"""
import os
import sys
import json
import shutil
import threading
import uuid
import asyncio
import time
import contextvars
from datetime import datetime

from fastapi import (
    FastAPI, UploadFile, Form, HTTPException, WebSocket, WebSocketDisconnect,
    Request)
from fastapi.responses import (
    FileResponse, StreamingResponse, HTMLResponse)
from fastapi.staticfiles import StaticFiles

from core import backend
from core.model_store import setup_model_env
setup_model_env()  # unify model cache dirs before whisper/funasr/babeldoc import
try:   # let downloaded market plugins hook into the app (best-effort)
    from core import plugins_registry as _pr
    _pr.activate_downloaded_plugins()
except Exception:  # noqa: BLE001
    pass
from webapp import sessions
from core.api_keys import (
    load_api_key_for_model, save_api_key_for_model, provider_of)
from core.languages_config import LABEL_TRANSLATIONS, LANGUAGE_MAP
from core.llm.online_translation import HardApiError, classify_fatal_error
from core.optional_modules import (
    module_status, realtime_voice_available,
    quick_voice_available, ocr_models, get_selected_ocr_model,
    extension_plugin_map)
from core.pipelines.video_translation_pipeline import (
    STT_MODELS, get_selected_stt_model, SENSEVOICE_SUPPORTED_CODES)
from core.log_config import app_logger

# In a PyInstaller one-file bundle the script runs from sys._MEIPASS (where the
# spec drops webapp/static); otherwise resolve relative to this file.
if getattr(sys, "frozen", False):
    STATIC_DIR = os.path.join(sys._MEIPASS, "webapp", "static")
    _ASSETS_DIR = os.path.join(sys._MEIPASS, "assets")
else:
    STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    _ASSETS_DIR = os.path.join(backend.REPO_ROOT, "assets")

# Web neural-VAD assets (onnxruntime WASM + Silero ONNX + JS loaders) must live in
# a WRITABLE dir: in a frozen build STATIC_DIR is under _MEIPASS, which is read-only
# when the app is installed to a protected location, so /api/ensure-web-vad's
# on-demand download would fail. Frozen -> use a runtime dir under data/ and seed it
# once from the bundled loaders/models; source run -> the writable static/vad.
if getattr(sys, "frozen", False):
    from core.paths import DATA_DIR as _DATA_DIR
    VAD_DIR = os.path.join(_DATA_DIR, "web_vad")
    _bundled_vad = os.path.join(STATIC_DIR, "vad")
    try:
        os.makedirs(VAD_DIR, exist_ok=True)
        if os.path.isdir(_bundled_vad):
            for _f in os.listdir(_bundled_vad):
                _dst = os.path.join(VAD_DIR, _f)
                if not os.path.exists(_dst):
                    shutil.copy2(os.path.join(_bundled_vad, _f), _dst)
    except Exception:  # noqa: BLE001 — fall back to the (read-only) bundle dir
        VAD_DIR = os.path.join(STATIC_DIR, "vad")
else:
    VAD_DIR = os.path.join(STATIC_DIR, "vad")
# Uploads must live OUTSIDE the translation temp dir: DocumentTranslator.process()
# wipes temp/ on a fresh run, which would delete the file being translated.
# DATA_DIR is the writable runtime data root (next to the exe in a frozen build),
# not the read-only bundle — so uploads work when installed to a read-only dir.
UPLOAD_DIR = os.path.join(backend.DATA_DIR, "web_uploads")

app = FastAPI(title="LinguaHaru Web")


@app.on_event("startup")
def _recover_interrupted_history():
    """Flip history rows left 'running' by a previous crash to 'interrupted' so
    they show up and can be continued. Sweeps the global (local-mode) DB; LAN
    per-session DBs are transient and skipped."""
    try:
        from core.translation_history import TranslationHistoryManager
        n = TranslationHistoryManager(
            log_dir=backend.get_custom_paths()[2]).mark_running_as_interrupted()
        if n:
            app_logger.info(f"Recovered {n} interrupted translation(s) from a previous run")
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.retention import run_retention
        run_retention()   # apply log + result disk retention
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.power import disable_background_throttling
        disable_background_throttling()   # full CPU speed even when backgrounded
    except Exception:  # noqa: BLE001
        pass
    try:
        import logging as _lg
        from core.log_config import install_excepthooks, file_logger, system_event
        install_excepthooks()
        # uvicorn server errors -> system log (NOT uvicorn.access — too noisy).
        file_logger.attach_to_logger("uvicorn.error", _lg.WARNING)
        system_event("LinguaHaru web server started")
    except Exception:  # noqa: BLE001
        pass


def server_mode_on():
    """Public-deploy mode: hide the key/model/admin UI, use the server's own
    key, and bind externally. On via the ``server_mode`` config flag, or
    automatically on Render (the ``RENDER`` env var is always set there)."""
    return bool(backend.get_config("server_mode", False)) or bool(os.environ.get("RENDER"))


def history_log_dir(session_id):
    """Where the translation-history DB lives for this request.

    Local single-user mode shares ONE global history DB with the Qt desktop app
    (data/log), so both frontends show the same records. LAN / server / deploy
    mode keeps history per browser session, so users on a shared host can't see
    each other's translations."""
    external = backend.get_config("lan_mode", False) or server_mode_on()
    if external:
        return sessions.session_paths(session_id)[2]
    return backend.get_custom_paths()[2]


# Carries the per-request admin token (set by the middleware) so the sync admin
# endpoints can check it without each taking a `request` parameter.
_admin_token = contextvars.ContextVar("admin_token", default="")
# Whether the request came from the local machine (loopback). The host's owner
# may always administer; remote LAN peers must authenticate.
_client_is_local = contextvars.ContextVar("client_is_local", default=True)


def _is_loopback(host):
    return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1") or (
        host or "").startswith("127.")


# Password hashing lives in core.backend so the Qt LAN toggle shares it.
_hash_pw = backend.hash_lan_password
_verify_pw = backend.verify_lan_password


def _block_in_server_mode():
    """Guard admin-only endpoints (changing the server's model/key, RPM, modules,
    interfaces). Always blocked in public server mode.

    In LAN mode the server binds 0.0.0.0, so it is reachable by other machines.
    The host's owner (loopback / localhost) may always administer. A REMOTE LAN
    peer must supply a valid admin password (X-Admin-Token) — and if no password
    has been configured at all, remote administration is refused outright (rather
    than left wide open). Outside LAN mode the bind is 127.0.0.1 only, so every
    caller is local and unrestricted."""
    if server_mode_on():
        raise HTTPException(403, "Disabled in server mode")
    if not backend.get_config("lan_mode", False):
        return  # bound to localhost; only the local user can reach this
    if _client_is_local.get():
        return  # the host machine's owner may always administer
    pw_hash = str(backend.get_config("lan_admin_password_hash", "") or "")
    if not pw_hash:
        raise HTTPException(403, "Remote administration disabled (no admin password set)")
    token = _admin_token.get()
    if not (token and _verify_pw(token, pw_hash)):
        raise HTTPException(401, "Admin password required")


@app.middleware("http")
async def _session_and_isolation(request, call_next):
    """Assign a per-browser session id (httponly cookie) used to isolate
    translation paths and proofreading across concurrent users, and set the
    cross-origin-isolation headers that ffmpeg.wasm needs for in-browser audio
    extraction ('credentialless' still lets us load the CDN core files)."""
    sid = request.cookies.get(sessions.SESSION_COOKIE)
    issue = not sessions.valid_session_id(sid)
    if issue:
        sid = sessions.new_session_id()
    request.state.session_id = sid
    _admin_token.set(request.headers.get("X-Admin-Token", ""))
    _client_is_local.set(_is_loopback(request.client.host if request.client else ""))
    resp = await call_next(request)
    if issue:
        resp.set_cookie(sessions.SESSION_COOKIE, sid, httponly=True,
                        samesite="lax", max_age=7 * 24 * 3600)
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    return resp

# task_id -> {progress, desc, status, output, error, stop}
TASKS = {}
_TASKS_LOCK = threading.Lock()

# Concurrency caps: each /api/translate spawns a background thread, so without a
# ceiling many LAN users (or repeated clicks) could pile up unbounded workers.
MAX_ACTIVE_TASKS = 6              # global across all sessions
MAX_ACTIVE_TASKS_PER_SESSION = 2  # per browser/session

# Input caps for the short-text / voice endpoints, so one oversized request can't
# tie up a translate worker, the STT lock, or memory for an unbounded time.
_MAX_LIVE_AUDIO_BYTES = 16000 * 2 * 32   # ~32s of 16kHz mono PCM16
_MAX_QUICK_TEXT_CHARS = 5000             # quick-translate / TTS / live captions
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024    # total bytes per /api/translate request


def _capped_text(payload, field="text", limit=_MAX_QUICK_TEXT_CHARS):
    """Return payload[field].strip(), or raise 413 if it exceeds `limit`. These
    endpoints serve short text (a phrase / one caption line), so a huge body is
    abuse, not a real use case."""
    text = (payload.get(field) or "").strip()
    if len(text) > limit:
        raise HTTPException(413, f"Text too long (max {limit} chars)")
    return text


def _prune_tasks(ttl=1800):
    """Drop finished tasks older than ttl (30 min) so the in-memory TASKS dict
    doesn't grow without bound over a long LAN session. The window is long
    enough that the user can still download the result."""
    now = time.time()
    with _TASKS_LOCK:
        dead = [tid for tid, s in TASKS.items()
                if s.get("status") in ("done", "error", "stopped")
                and now - s.get("ended_at", now) > ttl]
        for tid in dead:
            TASKS.pop(tid, None)


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Config / metadata
# --------------------------------------------------------------------------- #
@app.get("/api/bootstrap")
def bootstrap():
    """Everything the frontend needs on load."""
    config = backend.read_config()
    online = config.get("default_online", False)
    from core.llm.online_translation import _DEFAULT_RPM
    return {
        "languages": backend.available_languages(),
        "online_models": backend.scan_online_models(),
        "local_models": backend.scan_local_models(),
        "glossaries": backend.get_glossary_files(),
        "stt_models": [{"id": m["id"], "label": m["label"]} for m in STT_MODELS],
        "ocr_models": [{"id": m["id"], "label": m["label"]} for m in ocr_models()],
        "sensevoice_codes": sorted(SENSEVOICE_SUPPORTED_CODES),
        "language_map": LANGUAGE_MAP,
        "modules": module_status(),
        "ext_plugin": extension_plugin_map(),  # ext -> required plugin name
        "server_mode": server_mode_on(),
        "local_live_available": realtime_voice_available(),
        # "翻译语音输入" plugin: gates the Quick-Translate mic (STT) + speaker (TTS).
        "quick_voice_available": quick_voice_available(),
        "config": {
            "default_online": online,
            "default_online_model": config.get("default_online_model", ""),
            "default_src_lang": config.get("default_src_lang", "English"),
            "default_dst_lang": config.get("default_dst_lang", "中文"),
            "default_glossary": config.get("default_glossary", "Default"),
            "stt_model": get_selected_stt_model(),
            "ocr_model_size": get_selected_ocr_model(),
            "translate_subtitles": config.get("translate_subtitles", True),
            "max_retries": config.get("max_retries", 4),
            # Show the RPM that's actually in effect: an explicit user value, or
            # the safety-net default when unset (so the UI isn't misleading).
            "rpm_limit": config.get("rpm_limit", _DEFAULT_RPM),
            "auto_extract_glossary": config.get("auto_extract_glossary", False),
            "translation_mode": config.get("translation_mode", "precise"),
            "translation_tone": config.get("translation_tone", ""),
            "translation_length": config.get("translation_length", ""),
            "translation_style": config.get("translation_style", ""),
            "translate_with_context": config.get("translate_with_context", False),
            "mask_placeholders": config.get("mask_placeholders", True),
            "dedup_context": config.get("dedup_context", False),
            "bilingual_bold": config.get("bilingual_bold", True),
            "bilingual_color": config.get("bilingual_color", ""),
            "live_stream_translation": config.get("live_stream_translation", False),
            "web_vad": config.get("web_vad", "energy"),
            "live_vad_hang_ms": config.get("live_vad_hang_ms", 900),
            "live_vad_sensitivity": config.get("live_vad_sensitivity", "standard"),
            "live_vad_max_seg_ms": config.get("live_vad_max_seg_ms", 30000),
            "pdf_translate_table": config.get("pdf_translate_table", False),
            "pdf_ocr_scanned": config.get("pdf_ocr_scanned", False),
            "pdf_dual_alternating": config.get("pdf_dual_alternating", False),
            "pdf_pages": config.get("pdf_pages", ""),
            "pdf_only_translated_pages": config.get("pdf_only_translated_pages", False),
            "lan_mode": config.get("lan_mode", False),
            "has_lan_admin": bool(config.get("lan_admin_password_hash")),  # never expose the value
            "result_dir": config.get("result_dir", "data/result"),
            "history_max_records": config.get("history_max_records", 1000),
            "history_max_age_days": config.get("history_max_age_days", 0),
            "log_max_files": config.get("log_max_files", 500),
            "log_max_age_days": config.get("log_max_age_days", 30),
            "log_max_size_mb": config.get("log_max_size_mb", 500),
            "result_max_size_mb": config.get("result_max_size_mb", 5000),
            "default_thread_count_online": config.get("default_thread_count_online", 8),
            "default_thread_count_offline": config.get("default_thread_count_offline", 4),
            "thread_count": backend.thread_count_for_mode(
                online, config.get("default_online_model")),
        },
        "translation_modes": _translation_modes_for_ui(),
        "labels": LABEL_TRANSLATIONS,
    }


def _translation_modes_for_ui():
    """[{id, label, label_en}] for the translation-mode picker."""
    try:
        from core.translation_modes import load_modes
        out = []
        for mid, m in load_modes().items():
            out.append({"id": mid, "label": m.get("label", mid),
                        "label_en": m.get("label_en", mid)})
        return out
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/models")
def list_models():
    """Per-model state for the OCR + STT catalogs (install / delete / use) plus
    the unified download location."""
    from core import model_store
    from core.optional_modules import plugin_model_states
    from core.pipelines.video_translation_pipeline import stt_param_specs, get_stt_params
    stt = plugin_model_states("Video/Audio")
    for s in stt:   # attach per-model tunable params (STT only; OCR has none)
        specs = stt_param_specs(s["id"])
        if specs:
            s["params"] = specs
            s["param_values"] = get_stt_params(s["id"])
    return {"dir": model_store.current_dir(),
            "ocr": plugin_model_states("Image OCR"),
            "stt": stt}


@app.post("/api/models/params")
async def model_params(payload: dict):
    """Persist a model's tunable STT params (only non-defaults are stored)."""
    _block_in_server_mode()
    from core.pipelines.video_translation_pipeline import set_stt_params
    model_id, values = payload.get("model_id"), payload.get("values") or {}
    if not model_id:
        raise HTTPException(400, "model_id is required")
    return {"ok": True, "saved": set_stt_params(model_id, values)}


@app.get("/api/modules/models")
def modules_models(name: str):
    """A plugin's model catalog with per-model downloaded state + disk size +
    which is active — powers the plugin card's model-management picker."""
    from core.optional_modules import plugin_model_states, plugin_current_model
    return {"models": plugin_model_states(name, with_size=True),
            "current_model": plugin_current_model(name)}


@app.post("/api/models/select")
async def model_select(payload: dict):
    """Set the active model for a plugin (no download)."""
    _block_in_server_mode()
    from core.optional_modules import set_plugin_model
    name, model_id = payload.get("plugin"), payload.get("model_id")
    if not name or not model_id or not set_plugin_model(name, model_id):
        raise HTTPException(400, "plugin and a valid model_id are required")
    return {"ok": True}


@app.post("/api/models/delete")
async def model_delete(payload: dict):
    """Delete a specific model's files from disk."""
    _block_in_server_mode()
    from core.optional_modules import delete_plugin_model
    name, model_id = payload.get("plugin"), payload.get("model_id")
    if not name or not model_id:
        raise HTTPException(400, "plugin and model_id are required")
    return {"ok": bool(delete_plugin_model(name, model_id))}


@app.post("/api/config")
async def update_config(payload: dict):
    """Persist arbitrary settings keys (whitelisted)."""
    _block_in_server_mode()
    allowed = {"default_online", "default_online_model", "default_src_lang",
               "default_dst_lang", "default_glossary", "stt_model",
               "live_stt_model", "quick_stt_model", "ocr_model_size",
               "translate_subtitles",
               "max_retries", "rpm_limit",
               "auto_extract_glossary", "translation_mode",
               "translation_tone", "translation_length", "translation_style",
               "translate_with_context",
               "mask_placeholders", "dedup_context",
               "bilingual_bold", "bilingual_color", "live_stream_translation",
               "web_vad", "live_vad_hang_ms", "live_vad_sensitivity",
               "live_vad_max_seg_ms", "lan_mode",
               "result_dir", "history_max_records", "history_max_age_days",
               "log_max_files", "log_max_age_days", "log_max_size_mb",
               "result_max_size_mb",
               "default_thread_count_online", "default_thread_count_offline",
               "max_api_concurrency",
               "pdf_translate_table", "pdf_ocr_scanned", "pdf_dual_alternating",
               "pdf_pages", "pdf_only_translated_pages"}
    config = backend.read_config()
    for k, v in payload.items():
        # The LAN admin password is stored ONLY as a hash (system_config.json is
        # git-tracked — never persist the plaintext). Empty value clears it.
        if k == "lan_admin_password":
            if v:
                config["lan_admin_password_hash"] = _hash_pw(v)
            else:
                config.pop("lan_admin_password_hash", None)
            continue
        if k in allowed:
            config[k] = v
    config.pop("lan_admin_password", None)  # belt-and-suspenders: no plaintext
    backend.write_config(config)
    if "rpm_limit" in payload:  # apply the new RPM cap without a restart
        from core.llm.online_translation import reset_rpm_limit_cache
        reset_rpm_limit_cache()
    if any(k in payload for k in ("stt_model", "live_stt_model", "quick_stt_model")):
        # Switched an STT model -> free the previously-loaded one if now unused.
        try:
            from core.pipelines.video_translation_pipeline import release_unused_stt_models
            release_unused_stt_models()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Interface management (mirrors the Qt Interface page): local / official /
# custom provider cards, click to activate, configure params, add custom.
# --------------------------------------------------------------------------- #
def _resolve_active_interface(local, online_names):
    use_online = backend.get_config("default_online", True)
    online_active = backend.get_active_model(use_online=True)
    local_active = backend.get_active_model(use_online=False)
    if not use_online and local_active in local:
        return local_active
    if online_active in online_names:
        return online_active
    return online_names[0] if online_names else (local_active if local_active in local else "")


@app.get("/api/interfaces")
def list_interfaces():
    try:
        local = backend.scan_local_models()
    except Exception:  # noqa: BLE001 - probing is best-effort
        local = []
    online = backend.list_online_interfaces()
    online_names = [i["name"] for i in online]
    return {
        "active": _resolve_active_interface(local, online_names),
        "use_online": backend.get_config("default_online", True),
        "local": local,
        "online": online,
    }


@app.post("/api/interface/activate")
def activate_interface(payload: dict):
    _block_in_server_mode()
    name = payload.get("name")
    online = bool(payload.get("online", True))
    backend.set_active_model(name, use_online=online)
    backend.set_config("default_online", online)
    return {"ok": True, "active": name, "use_online": online}


@app.get("/api/interface/config")
def get_interface_config(name: str):
    _block_in_server_mode()
    cfg = backend.read_api_config(name) or {}
    return {"name": name, "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""), "temperature": cfg.get("temperature"),
            "top_p": cfg.get("top_p"),
            "rpm": cfg.get("rpm"), "thread_count": cfg.get("thread_count"),
            "max_retries": cfg.get("max_retries"),
            "has_key": bool(load_api_key_for_model(name))}


@app.post("/api/interface/save")
def save_interface(payload: dict):
    _block_in_server_mode()
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Interface name is required")
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    backend.write_api_config(name, {
        "base_url": (payload.get("base_url") or "").strip(),
        "model": (payload.get("model") or "").strip(),
        "temperature": _num(payload.get("temperature")),
        "top_p": _num(payload.get("top_p")),
        "rpm": _int(payload.get("rpm")),
        "thread_count": _int(payload.get("thread_count")),
        "max_retries": _int(payload.get("max_retries")),
    })
    key = payload.get("api_key")
    if key:  # only overwrite the stored key when a new one is supplied
        save_api_key_for_model(name, key.strip())
    return {"ok": True}


@app.post("/api/interface/delete")
def delete_interface(payload: dict):
    _block_in_server_mode()
    backend.delete_api_config(payload.get("name"))
    return {"ok": True}


@app.get("/api/apikey")
def get_apikey(model: str):
    """Whether a key exists for this model's provider (never returns the key)."""
    key = load_api_key_for_model(model)
    return {"provider": provider_of(model), "has_key": bool(key)}


@app.post("/api/apikey")
async def set_apikey(payload: dict):
    _block_in_server_mode()
    model = payload.get("model", "")
    key = payload.get("api_key", "")
    save_api_key_for_model(model, key)
    config = backend.read_config()
    config["remember_api_key"] = True
    backend.write_config(config)
    return {"ok": True, "provider": provider_of(model)}


# --------------------------------------------------------------------------- #
# Glossary
# --------------------------------------------------------------------------- #
@app.get("/api/glossary")
def get_glossary(name: str):
    import csv
    path = backend.glossary_path(name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"Glossary not found: {name}")
    for enc in ("utf-8-sig", "utf-8", "gbk", "shift-jis"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.reader(f))
        except UnicodeDecodeError:
            continue
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Failed to load glossary: {e}")
        columns = rows[0] if rows else []
        n = len(columns)
        # Pad/trim each data row to the header width so the grid stays rectangular.
        data = [(r + [""] * n)[:n] for r in rows[1:]]
        return {"columns": columns, "rows": data}
    raise HTTPException(500, "Failed to decode glossary file")


@app.post("/api/glossary")
async def save_glossary(payload: dict):
    _block_in_server_mode()   # shared glossary must not be writable on public deploys
    import csv
    name = payload.get("name")
    path = backend.glossary_path(name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"Glossary not found: {name}")
    columns = payload.get("columns", [])
    clean = [r for r in payload.get("rows", [])
             if "".join(str(c) for c in r).strip()]   # drop fully-empty rows
    if not clean and os.path.getsize(path) > 0:
        raise HTTPException(400, "Refused: empty table over a non-empty glossary.")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if columns:
            w.writerow(columns)
        w.writerows(clean)
    return {"ok": True, "count": len(clean)}


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
def _merge_coverage(acc, cov):
    """Sum two coverage reports (multi-file batches share one task_id)."""
    if not acc:
        return cov
    out = dict(acc)
    for k in ("total", "translated", "fallback", "needs_review"):
        out[k] = (acc.get(k, 0) or 0) + (cov.get(k, 0) or 0)
    by = dict(acc.get("by_category") or {})
    for cat, n in (cov.get("by_category") or {}).items():
        by[cat] = by.get(cat, 0) + n
    out["by_category"] = by
    return out


def _merge_qa(acc, qa):
    """Merge two qa warning dicts ({check: [ids/details]}) by concatenating lists."""
    if not acc:
        return qa
    out = {k: list(v) for k, v in acc.items()}
    for k, v in (qa or {}).items():
        out[k] = out.get(k, []) + list(v or [])
    return out


def _translate_one(task_id, session_id, file_path, model, use_online, src_lang,
                   dst_lang, glossary_name, bilingual_flags, on_progress,
                   continue_mode=False, resume_dirs=None, resume_record_id=None,
                   run_subdir=None, translation_id=None, batch_id=None, batch_size=None):
    """Translate a single file; returns its output path. Raises on failure.

    Paths are scoped to ``session_id`` so concurrent users never collide, and a
    stop is honored either per-task (this run) or per-session (the caller hit
    Stop / disconnected). When ``resume_dirs`` is given (a Continue from History)
    the run reuses that interrupted task's exact dirs in continue_mode."""
    ext = os.path.splitext(file_path)[1]
    stem = os.path.splitext(file_path)[0]
    translator_class = backend.get_translator_class(ext, **bilingual_flags)
    if translator_class is None:
        raise ValueError(f"Unsupported file type '{ext}'.")

    # In server mode the key comes from the server (mykeys, or LINGUAHARU_API_KEY
    # for a keyless Render deploy) — public users never supply one.
    api_key = (load_api_key_for_model(model)
               or os.environ.get("LINGUAHARU_API_KEY", "")) if use_online else ""
    src_code = backend.language_code(src_lang)
    dst_code = backend.language_code(dst_lang)
    gpath = backend.glossary_path(glossary_name) if glossary_name else None
    if resume_dirs:
        temp_dir, result_dir, log_dir = resume_dirs
    else:
        temp_dir, result_dir, log_dir = sessions.session_paths(session_id)
        # Per-task subdir so two same-named files in ONE session don't collide
        # (DocumentTranslator.file_dir is derived from basename). Named by the run
        # start datetime (+ a short id for uniqueness) so each task gets its own
        # readable folder, matching the Qt desktop layout; falls back to task_id.
        sub = run_subdir or task_id
        temp_dir = os.path.join(temp_dir, sub)
        result_dir = os.path.join(result_dir, sub)
        log_dir = os.path.join(log_dir, sub)
    for _d in (temp_dir, result_dir, log_dir):
        os.makedirs(_d, exist_ok=True)
    config = backend.read_config()

    # The per-project log is opened by base_translator.process() into the result
    # folder, bound to this run's context (isolated even under concurrency).

    translator = translator_class(
        file_path, model, use_online, api_key, src_code, dst_code, continue_mode,
        max_token=backend.max_token_for_model(model if use_online else None),
        max_retries=backend.max_retries_for_model(model if use_online else None),
        thread_count=backend.thread_count_for_mode(use_online, model),
        glossary_path=gpath, temp_dir=temp_dir, result_dir=result_dir,
        session_lang="en", log_dir=log_dir,
        history_dir=history_log_dir(session_id),
        batch_id=batch_id, batch_size=batch_size,
    )
    # Reuse a pre-assigned id (batch pre-registration) or the resume row's id, so
    # this file's records update that exact history row instead of duplicating.
    if translation_id or resume_record_id:
        translator.translation_id = translation_id or resume_record_id
    # Expose THIS file's history-row id on the task so /api/pause and /api/resume
    # can mirror the live status (running <-> paused) onto its record.
    with _TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]["translation_id"] = translator.translation_id
    # Captured into the history record if this run fails/stops, so a later
    # Continue can reconstruct THIS run (langs/model/glossary/bilingual + dirs).
    translator.resume_info = {
        "src_lang": src_lang, "dst_lang": dst_lang,
        "model": model, "use_online": use_online,
        "glossary_name": glossary_name, "bilingual_flags": bilingual_flags,
        "input_file_path": file_path,
        "temp_dir": temp_dir, "result_dir": result_dir, "log_dir": log_dir,
    }

    def check_stop():
        # Task-scoped control checkpoint (called all over the backend loops):
        #   * Stop  -> raise, abort this task (others in the session keep running).
        #   * Pause -> block IN PLACE until resumed or stopped. The thread/process/
        #     models stay alive, so resume continues from this exact point (true
        #     pause, not a restart). Never hold _TASKS_LOCK while sleeping — a held
        #     lock here could deadlock BabelDOC's internal worker threads.
        while True:
            with _TASKS_LOCK:
                t = TASKS.get(task_id, {})
                if t.get("stop"):
                    raise RuntimeError("__stopped__")
                if not t.get("paused"):
                    return
            time.sleep(0.15)
    translator.check_stop_requested = check_stop
    output_path, _missing = translator.process(
        stem, ext, progress_callback=lambda v, desc=None: (check_stop(), on_progress(v, desc)))

    # Accumulate this file's token usage onto the task (summed across files) so
    # the 'done' event can show a thank-you with total tokens + cost.
    with _TASKS_LOCK:
        t = TASKS.get(task_id)
        if t is not None:
            t["tok_prompt"] = t.get("tok_prompt", 0) + getattr(translator, "total_prompt_tokens", 0)
            t["tok_completion"] = t.get("tok_completion", 0) + getattr(translator, "total_completion_tokens", 0)
            t["tokens"] = t.get("tokens", 0) + getattr(translator, "total_tokens", 0)

    # Translation coverage (best-effort): base_translator drops coverage.json in
    # the result dir; ACCUMULATE across the batch's files (a multi-file run shares
    # one task_id, so overwriting would only show the last file's numbers).
    try:
        cov_path = os.path.join(result_dir, "coverage.json")
        if os.path.exists(cov_path):
            with open(cov_path, "r", encoding="utf-8") as f:
                cov = json.load(f)
            with _TASKS_LOCK:
                if task_id in TASKS:
                    TASKS[task_id]["coverage"] = _merge_coverage(
                        TASKS[task_id].get("coverage"), cov)
    except Exception:  # noqa: BLE001
        pass

    # Quality-check warnings (best-effort): base_translator drops qa.json next to
    # coverage.json; merge across the batch's files (same task_id).
    try:
        qa_path = os.path.join(result_dir, "qa.json")
        if os.path.exists(qa_path):
            with open(qa_path, "r", encoding="utf-8") as f:
                qa = json.load(f)
            if qa:
                with _TASKS_LOCK:
                    if task_id in TASKS:
                        TASKS[task_id]["qa"] = _merge_qa(TASKS[task_id].get("qa"), qa)
    except Exception:  # noqa: BLE001
        pass
    return output_path


def _friendly_api_error(error, lang="en"):
    """Localized, category-specific message for a fatal API error (matches the
    Qt worker's wording so both frontends read the same)."""
    category = getattr(error, "category", None) or classify_fatal_error(str(error))
    keys = {
        "insufficient_balance": "Err Insufficient Balance",
        "invalid_key": "Err Invalid Key",
        "server_error": "Err Server",
    }
    key = keys.get(category, "Err Api Generic")
    labels = LABEL_TRANSLATIONS.get(lang) or {}
    return (labels.get(key)
            or LABEL_TRANSLATIONS.get("en", {}).get(key)
            or "Translation stopped due to an API error.")


def _precreate_web_batch(session_id, batch_id, file_paths, file_ids, model,
                         use_online, src_lang, dst_lang, glossary_name, bilingual_flags):
    """Register a 'queued' history row per file (one batch = this task) so the
    History page shows all files grouped under one parent immediately."""
    try:
        from core.translation_history import (
            TranslationHistoryManager, create_translation_record)
        mgr = TranslationHistoryManager(log_dir=history_log_dir(session_id))
        src_code, dst_code = backend.language_code(src_lang), backend.language_code(dst_lang)
        now = datetime.now()
        n = len(file_paths)
        for fp, tid in zip(file_paths, file_ids):
            mgr.add_record(create_translation_record(
                translation_id=tid, start_time=now, end_time=now, total_tokens=0,
                src_lang=src_code, src_lang_display=src_lang,
                dst_lang=dst_code, dst_lang_display=dst_lang,
                model=model, use_online=use_online,
                input_file=os.path.basename(fp), output_file_path="", log_file_path="",
                status="queued",
                resume_info={"input_file_path": fp, "src_lang": src_lang,
                             "dst_lang": dst_lang, "model": model, "use_online": use_online,
                             "glossary_name": glossary_name, "bilingual_flags": bilingual_flags},
                batch_id=batch_id, batch_size=n))
    except Exception:  # noqa: BLE001 — pre-registration must never block a run
        pass


def _mark_web_queued_stopped(session_id, file_ids):
    """Flip still-'queued' rows (files that never started) to 'stopped'."""
    try:
        from core.translation_history import TranslationHistoryManager
        mgr = TranslationHistoryManager(log_dir=history_log_dir(session_id))
        for tid in file_ids:
            mgr.set_status(tid, "stopped")
    except Exception:  # noqa: BLE001
        pass


def _run_with_power(fn, *args):
    """Thread target wrapper: keep the machine awake/un-throttled for the whole
    run, then release it. Used for every translation/resume background thread."""
    from core.power import keep_awake
    with keep_awake():
        fn(*args)


def _run_translation(task_id, session_id, file_paths, model, use_online,
                     src_lang, dst_lang, glossary_name, bilingual_flags, ui_lang="en"):
    """Background worker: translate one or more files; zip when more than one."""
    def set_state(**kw):
        with _TASKS_LOCK:
            TASKS[task_id].update(kw)
            if kw.get("status") in ("done", "error", "stopped"):
                TASKS[task_id]["ended_at"] = time.time()

    def _usage_summary():
        """{"tokens", "cost":{amount,symbol,currency}} from accumulated usage."""
        with _TASKS_LOCK:
            t = TASKS.get(task_id, {})
            tp, tc, tt = t.get("tok_prompt", 0), t.get("tok_completion", 0), t.get("tokens", 0)
        out = {"tokens": tt, "cost": None}
        if use_online and tt > 0:
            try:
                from core.pricing import estimate_cost
                amt, sym, ccy = estimate_cost(model, tp, tc, ui_lang)
                out["cost"] = {"amount": round(amt, 4), "symbol": sym, "currency": ccy}
            except Exception:  # noqa: BLE001
                pass
        return out

    total = len(file_paths)
    # One readable, unique folder per run (start datetime + short id), so each
    # task's outputs land in their own dir instead of all piling into the session
    # result folder — matches the Qt desktop layout.
    run_folder = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{task_id[:6]}"
    # One batch = this task: pre-register a "queued" row per file so the History
    # page shows all N grouped under one parent from the start.
    file_ids = [uuid.uuid4().hex for _ in file_paths]
    _precreate_web_batch(session_id, task_id, file_paths, file_ids, model,
                         use_online, src_lang, dst_lang, glossary_name, bilingual_flags)
    outputs, file_results = [], []
    try:
        for idx, fp in enumerate(file_paths):
            name = os.path.basename(fp)

            def on_progress(v, desc=None, _idx=idx, _name=name):
                overall = (_idx + float(v)) / total
                set_state(progress=overall, desc=(f"[{_idx+1}/{total}] {_name}: " + (desc or "")))

            on_progress(0.0, "Extracting text...")
            try:
                outputs.append(_translate_one(
                    task_id, session_id, fp, model, use_online, src_lang,
                    dst_lang, glossary_name, bilingual_flags, on_progress,
                    run_subdir=run_folder, translation_id=file_ids[idx],
                    batch_id=task_id, batch_size=total))
                file_results.append((name, "success", ""))
            except HardApiError as e:
                # Account-level fault (insufficient balance / invalid key): every
                # remaining file would fail the same way, so abort the whole batch
                # with a clear, localized message instead of N identical errors.
                msg = _friendly_api_error(e, ui_lang)
                app_logger.error(f"Web translation aborted [{getattr(e,'category','api_error')}]: {e}")
                _mark_web_queued_stopped(session_id, file_ids[idx:])
                set_state(status="error", error=msg)
                return
            except RuntimeError as e:
                if "__stopped__" in str(e):
                    raise
                file_results.append((name, "failed", str(e)))
            except Exception as e:  # noqa: BLE001
                app_logger.exception(f"Web translation error for {name}")
                file_results.append((name, "failed", str(e)))

        if not outputs:
            set_state(status="error", error="All files failed. See log.")
        elif total == 1:
            u = _usage_summary()
            set_state(status="done", progress=1.0, desc="Translation completed",
                      output=outputs[0], tokens=u["tokens"], cost=u["cost"])
        else:
            zip_path = backend.zip_results(outputs, file_results)
            ok = sum(1 for _, s, _ in file_results if s == "success")
            u = _usage_summary()
            set_state(status="done", progress=1.0,
                      desc=f"Translation completed ({ok}/{total})", output=zip_path,
                      tokens=u["tokens"], cost=u["cost"])
    except RuntimeError as e:
        if "__stopped__" in str(e):
            # Files not yet processed never started — flip their queued rows to
            # stopped (the in-flight file's translator already wrote "stopped").
            _mark_web_queued_stopped(session_id, file_ids[len(file_results):])
            set_state(status="stopped", desc="Stopped")
        else:
            app_logger.exception("Web translation error")
            set_state(status="error", error=str(e))


@app.post("/api/translate")
async def translate(
    request: Request,
    files: list[UploadFile],
    src_lang: str = Form(...),
    dst_lang: str = Form(...),
    model: str = Form(...),
    use_online: bool = Form(True),
    glossary: str = Form(""),
    bilingual: bool = Form(False),
    ui_lang: str = Form("en"),
):
    session_id = request.state.session_id
    _prune_tasks()  # drop stale finished tasks before accounting
    # Cap concurrent background translations (global + per-session) so repeated
    # clicks or many LAN users can't pile up unbounded worker threads.
    with _TASKS_LOCK:
        running = [t for t in TASKS.values() if t.get("status") == "running"]
        if len(running) >= MAX_ACTIVE_TASKS:
            raise HTTPException(
                429, "Server is busy (too many active translations). Please retry shortly.")
        if sum(1 for t in running if t.get("session_id") == session_id) >= MAX_ACTIVE_TASKS_PER_SESSION:
            raise HTTPException(
                429, "You already have the maximum number of translations running. Please wait.")

    # One upload dir per task (nested under the session) so concurrent or
    # same-named uploads never clobber each other.
    task_id = uuid.uuid4().hex[:12]
    upload_dir = os.path.join(UPLOAD_DIR, session_id, task_id)
    os.makedirs(upload_dir, exist_ok=True)
    dests = []
    written = 0
    for f in files:
        dest = os.path.join(upload_dir, os.path.basename(f.filename or "upload"))
        # Stream with a hard cap so a remote/LAN peer can't exhaust disk/memory
        # (document uploads were the one unbounded input — short-text/audio are
        # already capped).
        with open(dest, "wb") as out:
            while True:
                chunk = f.file.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    raise HTTPException(413, "Upload too large")
                out.write(chunk)
        dests.append(dest)

    with _TASKS_LOCK:
        TASKS[task_id] = {"progress": 0.0, "desc": "Queued...",
                          "status": "running", "output": None, "error": None,
                          "stop": False, "paused": False, "session_id": session_id}
    flags = {k: bilingual for k in (
        "excel_bilingual_mode", "word_bilingual_mode", "pdf_bilingual_mode",
        "subtitle_bilingual_mode", "txt_bilingual_mode", "md_bilingual_mode",
        "epub_bilingual_mode", "html_bilingual_mode")}
    # System-log the enqueue (no source text): short ids, file count, model, langs.
    from core.log_config import system_event
    system_event(f"Web task {task_id[:6]} (session {session_id[:6]}): "
                 f"{len(dests)} file(s), {written // 1024} KB, "
                 f"{model} ({'online' if use_online else 'offline'}), "
                 f"{src_lang}->{dst_lang}")
    threading.Thread(target=_run_with_power, args=(
        _run_translation,
        task_id, session_id, dests, model, use_online, src_lang, dst_lang,
        glossary, flags, ui_lang), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
def progress(task_id: str, request: Request):
    with _TASKS_LOCK:
        owner = TASKS.get(task_id, {}).get("session_id")
    if owner is None or owner != request.state.session_id:
        raise HTTPException(404, "Unknown task")

    def stream():
        import time
        last = None
        deadline = time.time() + 6 * 3600   # safety bound: never pin a worker forever
        while True:
            with _TASKS_LOCK:
                state = dict(TASKS.get(task_id, {}))
            if not state or time.time() > deadline:
                break   # task pruned/gone, or safety deadline hit
            snapshot = (round(state.get("progress", 0), 4), state.get("desc"),
                        state.get("status"), state.get("paused"))
            if snapshot != last:
                last = snapshot
                payload = {k: state.get(k) for k in ('progress', 'desc', 'status', 'error')}
                payload["paused"] = bool(state.get("paused"))
                if state.get("coverage") is not None:
                    payload["coverage"] = state.get("coverage")
                if state.get("qa"):
                    payload["qa"] = state.get("qa")
                if state.get("status") == "done":
                    payload["tokens"] = state.get("tokens", 0)
                    payload["cost"] = state.get("cost")
                yield f"data: {json.dumps(payload)}\n\n"
            if state.get("status") in ("done", "error", "stopped"):
                break
            time.sleep(0.2)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/stop/{task_id}")
def stop(task_id: str, request: Request):
    sid = request.state.session_id
    with _TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is not None and task.get("session_id") == sid:
            task["stop"] = True   # task-scoped; other tasks in this session keep running
            task["paused"] = False   # wake a paused task so its blocked threads see the stop
    return {"ok": True}


def _mirror_task_status(session_id, task, status):
    """Reflect the live task status (running/paused) onto the in-flight file's
    history row so the History page mirrors the real state. Best-effort."""
    tid = task.get("translation_id")
    if not tid:
        return
    try:
        from core.translation_history import TranslationHistoryManager
        TranslationHistoryManager(log_dir=history_log_dir(session_id)).set_status(tid, status)
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/pause/{task_id}")
def pause(task_id: str, request: Request):
    """Freeze a running task in place (true pause — nothing is torn down, so a
    later resume continues from the exact point). Task-scoped."""
    sid = request.state.session_id
    paused_task = None
    with _TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is not None and task.get("session_id") == sid and task.get("status") == "running":
            task["paused"] = True
            paused_task = dict(task)
    if paused_task:
        _mirror_task_status(sid, paused_task, "paused")
    return {"ok": True}


@app.post("/api/resume/{task_id}")
def resume(task_id: str, request: Request):
    """Resume a paused task; its blocked worker threads continue from where they
    stopped checking."""
    sid = request.state.session_id
    resumed_task = None
    with _TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is not None and task.get("session_id") == sid:
            task["paused"] = False
            resumed_task = dict(task)
    if resumed_task:
        _mirror_task_status(sid, resumed_task, "running")
    return {"ok": True}


@app.get("/api/download/{task_id}")
def download(task_id: str, request: Request):
    sid = request.state.session_id
    with _TASKS_LOCK:
        state = dict(TASKS.get(task_id, {}))
    # Only the task's owner may download its result.
    if state.get("session_id") != sid:
        raise HTTPException(404, "Result not ready")
    out = state.get("output")
    if not out or not os.path.exists(out):
        raise HTTPException(404, "Result not ready")
    return FileResponse(out, filename=os.path.basename(out))


# --------------------------------------------------------------------------- #
# History (scoped to the caller's session)
# --------------------------------------------------------------------------- #
@app.get("/api/history")
def history(request: Request, limit: int = 200, file_type: str = "",
            sort_by: str = "start_time", desc: bool = True, status: str = ""):
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(request.state.session_id))
    records = h.get_all_records(limit=limit, file_type=(file_type or None),
                                sort_by=sort_by, descending=desc,
                                status=(status or None))
    return {"records": records, "file_types": h.file_types()}


@app.post("/api/history/clear")
def history_clear(request: Request, payload: dict = None):
    """Clear this session's translation history (incl. real-time-voice records).
    With {"with_files": true} also delete the OUTPUT/LOG files those records
    produced (never the user's original input files)."""
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(request.state.session_id))
    if payload and payload.get("with_files"):
        info = h.clear_all_records_and_files()
        return {"ok": True, "files_deleted": info.get("files_deleted", 0)}
    return {"ok": bool(h.clear_all_records())}


def _run_resume(task_id, session_id, rec, ui_lang="en"):
    """Background worker: continue an interrupted translation from its record."""
    def set_state(**kw):
        with _TASKS_LOCK:
            if task_id in TASKS:
                TASKS[task_id].update(kw)
                if kw.get("status") in ("done", "error", "stopped"):
                    TASKS[task_id]["ended_at"] = time.time()

    try:
        info = json.loads(rec.get("resume_info") or "{}")
    except (ValueError, TypeError):
        info = {}
    src = info.get("input_file_path")
    name = os.path.basename(src or rec.get("input_file") or "file")
    model = info.get("model") or rec.get("model")
    use_online = info.get("use_online", rec.get("use_online", True))

    def on_progress(v, desc=None):
        set_state(progress=float(v), desc=(f"{name}: " + (desc or "")))

    try:
        out = _translate_one(
            task_id, session_id, src, model, use_online,
            info.get("src_lang"), info.get("dst_lang"),
            info.get("glossary_name", ""), info.get("bilingual_flags", {}),
            on_progress, continue_mode=True,
            resume_dirs=(info["temp_dir"], info["result_dir"], info["log_dir"]),
            resume_record_id=rec.get("id"))
        with _TASKS_LOCK:
            t = TASKS.get(task_id, {})
            tp, tc, tt = t.get("tok_prompt", 0), t.get("tok_completion", 0), t.get("tokens", 0)
        cost = None
        if use_online and tt > 0:
            try:
                from core.pricing import estimate_cost
                amt, sym, ccy = estimate_cost(model, tp, tc, ui_lang)
                cost = {"amount": round(amt, 4), "symbol": sym, "currency": ccy}
            except Exception:  # noqa: BLE001
                pass
        set_state(status="done", progress=1.0, desc="Translation completed",
                  output=out, tokens=tt, cost=cost)
    except HardApiError as e:
        app_logger.error(f"Web resume aborted [{getattr(e,'category','api_error')}]: {e}")
        set_state(status="error", error=_friendly_api_error(e, ui_lang))
    except RuntimeError as e:
        if "__stopped__" in str(e):
            set_state(status="stopped", desc="Stopped")
        else:
            app_logger.exception("Web resume error")
            set_state(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        app_logger.exception("Web resume error")
        set_state(status="error", error=str(e))


@app.post("/api/history/resume")
def history_resume(payload: dict, request: Request):
    """Continue an interrupted (failed/stopped) translation. Returns a task_id
    the client polls via /api/progress, exactly like a normal translation."""
    sid = request.state.session_id
    rid = (payload or {}).get("id")
    ui_lang = (payload or {}).get("ui_lang", "en")
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(sid))
    rec = h.get_record_by_id(rid) if rid else None
    if not rec:
        raise HTTPException(404, "Record not found")
    if rec.get("status") not in ("failed", "stopped", "interrupted"):
        raise HTTPException(400, "Only interrupted translations can be continued")
    try:
        info = json.loads(rec.get("resume_info") or "{}")
    except (ValueError, TypeError):
        info = {}
    src = info.get("input_file_path")
    if not info or not src or not os.path.exists(src):
        # The uploaded source was cleaned up; the user must re-upload to redo it.
        labels = LABEL_TRANSLATIONS.get(ui_lang) or LABEL_TRANSLATIONS.get("en", {})
        raise HTTPException(409, labels.get("Source File Missing",
                                            "The source file no longer exists."))

    _prune_tasks()
    with _TASKS_LOCK:
        running = [t for t in TASKS.values() if t.get("status") == "running"]
        if len(running) >= MAX_ACTIVE_TASKS:
            raise HTTPException(429, "Server is busy. Please retry shortly.")
        if sum(1 for t in running if t.get("session_id") == sid) >= MAX_ACTIVE_TASKS_PER_SESSION:
            raise HTTPException(429, "You already have the maximum number of translations running.")

    task_id = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        TASKS[task_id] = {"progress": 0.0, "desc": "Queued...", "status": "running",
                          "output": None, "error": None, "stop": False,
                          "paused": False, "session_id": sid}
    threading.Thread(target=_run_with_power, args=(_run_resume, task_id, sid, rec, ui_lang),
                     daemon=True).start()
    return {"task_id": task_id}


def _run_resume_batch(task_id, session_id, recs, ui_lang="en"):
    """Continue MULTIPLE records (a whole batch) as one task, processed
    sequentially — mirrors _run_translation but resumes each record from its own
    resume_info (continue_mode if it has dirs, else a fresh run reusing its id)."""
    def set_state(**kw):
        with _TASKS_LOCK:
            if task_id in TASKS:
                TASKS[task_id].update(kw)
                if kw.get("status") in ("done", "error", "stopped"):
                    TASKS[task_id]["ended_at"] = time.time()

    total = len(recs)
    run_folder = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{task_id[:6]}"
    outputs, file_results = [], []
    last_model, last_online = "", False
    try:
        for idx, rec in enumerate(recs):
            try:
                info = json.loads(rec.get("resume_info") or "{}")
            except (ValueError, TypeError):
                info = {}
            src = info.get("input_file_path")
            name = os.path.basename(src or rec.get("input_file") or "file")
            model = info.get("model") or rec.get("model")
            use_online = info.get("use_online", rec.get("use_online", True))
            last_model, last_online = model, use_online
            dirs = (info.get("temp_dir"), info.get("result_dir"), info.get("log_dir"))
            fresh = not all(dirs)

            def on_progress(v, desc=None, _i=idx, _n=name):
                set_state(progress=(_i + float(v)) / total,
                          desc=(f"[{_i+1}/{total}] {_n}: " + (desc or "")))

            on_progress(0.0, "Extracting text...")
            try:
                outputs.append(_translate_one(
                    task_id, session_id, src, model, use_online,
                    info.get("src_lang"), info.get("dst_lang"),
                    info.get("glossary_name", ""), info.get("bilingual_flags", {}),
                    on_progress, continue_mode=not fresh,
                    resume_dirs=None if fresh else dirs,
                    resume_record_id=rec.get("id"),
                    run_subdir=(run_folder if fresh else None),
                    batch_id=rec.get("batch_id") or None,
                    batch_size=rec.get("batch_size") or total))
                file_results.append((name, "success", ""))
            except HardApiError as e:
                set_state(status="error", error=_friendly_api_error(e, ui_lang))
                return
            except RuntimeError as e:
                if "__stopped__" in str(e):
                    raise
                file_results.append((name, "failed", str(e)))
            except Exception as e:  # noqa: BLE001
                app_logger.exception(f"Web batch-resume error for {name}")
                file_results.append((name, "failed", str(e)))

        with _TASKS_LOCK:
            t = TASKS.get(task_id, {})
            tp, tc, tt = t.get("tok_prompt", 0), t.get("tok_completion", 0), t.get("tokens", 0)
        cost = None
        if last_online and tt > 0:
            try:
                from core.pricing import estimate_cost
                amt, sym, ccy = estimate_cost(last_model, tp, tc, ui_lang)
                cost = {"amount": round(amt, 4), "symbol": sym, "currency": ccy}
            except Exception:  # noqa: BLE001
                pass
        if not outputs:
            set_state(status="error", error="All files failed. See log.")
        elif total == 1:
            set_state(status="done", progress=1.0, desc="Translation completed",
                      output=outputs[0], tokens=tt, cost=cost)
        else:
            zip_path = backend.zip_results(outputs, file_results)
            ok = sum(1 for _, s, _ in file_results if s == "success")
            set_state(status="done", progress=1.0,
                      desc=f"Translation completed ({ok}/{total})", output=zip_path,
                      tokens=tt, cost=cost)
    except RuntimeError as e:
        if "__stopped__" in str(e):
            set_state(status="stopped", desc="Stopped")
        else:
            app_logger.exception("Web batch-resume error")
            set_state(status="error", error=str(e))


@app.post("/api/history/resume-batch")
def history_resume_batch(payload: dict, request: Request):
    """Continue ALL resumable records of a batch at once (one task, sequential)."""
    sid = request.state.session_id
    ids = (payload or {}).get("ids") or []
    ui_lang = (payload or {}).get("ui_lang", "en")
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(sid))
    recs = []
    for rid in ids:
        rec = h.get_record_by_id(rid)
        if not rec or rec.get("status") not in ("failed", "stopped", "interrupted"):
            continue
        try:
            info = json.loads(rec.get("resume_info") or "{}")
        except (ValueError, TypeError):
            info = {}
        src = info.get("input_file_path")
        if info and src and os.path.exists(src):   # skip records whose upload is gone
            recs.append(rec)
    if not recs:
        labels = LABEL_TRANSLATIONS.get(ui_lang) or LABEL_TRANSLATIONS.get("en", {})
        raise HTTPException(409, labels.get("Source File Missing",
                                            "The source file no longer exists."))
    _prune_tasks()
    with _TASKS_LOCK:
        running = [t for t in TASKS.values() if t.get("status") == "running"]
        if len(running) >= MAX_ACTIVE_TASKS:
            raise HTTPException(429, "Server is busy. Please retry shortly.")
        if sum(1 for t in running if t.get("session_id") == sid) >= MAX_ACTIVE_TASKS_PER_SESSION:
            raise HTTPException(429, "You already have the maximum number of translations running.")
    task_id = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        TASKS[task_id] = {"progress": 0.0, "desc": "Queued...", "status": "running",
                          "output": None, "error": None, "stop": False,
                          "paused": False, "session_id": sid}
    threading.Thread(target=_run_with_power, args=(_run_resume_batch, task_id, sid, recs, ui_lang),
                     daemon=True).start()
    return {"task_id": task_id}


@app.post("/api/history/delete")
def history_delete(payload: dict, request: Request):
    """Delete one record AND all of its data: the output + log files we
    generated and the per-file temp working dir (never the user's original)."""
    sid = request.state.session_id
    rid = (payload or {}).get("id")
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(sid))
    rec = h.get_record_by_id(rid) if rid else None
    if not rec:
        raise HTTPException(404, "Record not found")
    for key in ("output_file_path", "log_file_path"):
        p = rec.get(key)
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    try:
        info = json.loads(rec.get("resume_info") or "{}")
    except (ValueError, TypeError):
        info = {}
    temp_dir = info.get("temp_dir")
    src = info.get("input_file_path") or rec.get("input_file")
    if temp_dir and src:
        file_dir = os.path.join(temp_dir, os.path.splitext(os.path.basename(src))[0])
        if os.path.isdir(file_dir):
            shutil.rmtree(file_dir, ignore_errors=True)
    return {"ok": bool(h.delete_record(rid))}


@app.get("/api/history/download")
def history_download(id: str, request: Request):
    """Download the output file a history record produced (the web equivalent of
    'open folder')."""
    sid = request.state.session_id
    from core.translation_history import TranslationHistoryManager
    h = TranslationHistoryManager(log_dir=history_log_dir(sid))
    rec = h.get_record_by_id(id) if id else None
    out = rec.get("output_file_path") if rec else None
    if not out or not os.path.exists(out):
        raise HTTPException(404, "Output not available")
    return FileResponse(out, filename=os.path.basename(out))


@app.post("/api/pick-folder")
def pick_folder():
    """Open a native folder picker ON THE SERVER machine (local desktop use) and
    return the chosen absolute path. Runs in a subprocess so tkinter never
    touches the server's asyncio loop. Disabled on public/shared deploys, and
    admin-gated so a remote LAN peer can't pop a dialog on the host's desktop
    (it's a local-desktop convenience only)."""
    _block_in_server_mode()
    import subprocess
    import sys
    code = ("import tkinter, sys\n"
            "from tkinter import filedialog\n"
            "r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            "sys.stdout.write(filedialog.askdirectory() or '')\n")
    try:
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Folder picker failed: {e}")
    return {"path": (out.stdout or "").strip()}


# --------------------------------------------------------------------------- #
# Proofread (scoped to the caller's session; IDOR / traversal protected)
# --------------------------------------------------------------------------- #
@app.get("/api/proofread/docs")
def proofread_docs(request: Request):
    return {"docs": sessions.list_proofread_docs(request.state.session_id)}


@app.get("/api/proofread")
def proofread_load(name: str, request: Request):
    if sessions.proofread_doc_dir(name, request.state.session_id) is None:
        raise HTTPException(404, "Translation data not found")
    try:
        rows = backend.load_proofread_table(name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    # rows: (count_src, original, translated)
    return {"columns": ["count_src", "Original", "Translation"],
            "rows": [list(r) for r in rows]}


@app.post("/api/proofread")
async def proofread_save(payload: dict, request: Request):
    name = payload.get("name")
    if sessions.proofread_doc_dir(name, request.state.session_id) is None:
        raise HTTPException(404, "Translation data not found")
    rows = [tuple(r) for r in payload.get("rows", [])]
    try:
        changed = backend.save_proofread_table(name, rows)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"ok": True, "changed": changed}


@app.post("/api/proofread/export")
async def proofread_export(payload: dict, request: Request):
    name = payload.get("name")
    sid = request.state.session_id
    if sessions.proofread_doc_dir(name, sid) is None:
        raise HTTPException(404, "Translation data not found")
    try:
        path = backend.export_proofread_doc(name)
        # Move the export into the caller's session result dir so two users
        # proofreading same-named docs can't read each other's output.
        _, result_dir, _ = sessions.session_paths(sid)
        dest = os.path.join(result_dir, os.path.basename(path))
        if os.path.realpath(dest) != os.path.realpath(path):
            os.replace(path, dest)
            path = dest
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    EXPORTS[(sid, name)] = path   # per-session key: same-named docs across users don't collide
    return {"ok": True, "filename": os.path.basename(path)}


EXPORTS = {}  # (session_id, doc_name) -> exported file path


@app.get("/api/proofread/download")
def proofread_download(name: str, request: Request):
    sid = request.state.session_id
    if sessions.proofread_doc_dir(name, sid) is None:
        raise HTTPException(404, "Export not ready")
    path = EXPORTS.get((sid, name))
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Export not ready")
    return FileResponse(path, filename=os.path.basename(path))


# --------------------------------------------------------------------------- #
# Optional module install / uninstall (runs pip in the background)
# --------------------------------------------------------------------------- #
MODULE_JOBS = {}  # name -> {"status": running|done|error, "output": str}
# pip/uv install/uninstall + model downloads all mutate the SAME interpreter env;
# running two at once corrupts it. Serialize every job through one lock, and refuse
# a new job (409) while one is running.
_MODULE_JOB_LOCK = threading.Lock()


def _module_busy():
    with _TASKS_LOCK:
        return any(j.get("status") == "running" for j in MODULE_JOBS.values())


import queue as _queue
# Plugin jobs (install/uninstall/upgrade/model-download) all mutate the SAME
# interpreter env, so they can't run in parallel — but instead of rejecting a
# second request (409) we QUEUE it and run jobs one at a time, FIFO. A single
# worker thread = serial by construction.
_MODULE_QUEUE = _queue.Queue()
_MODULE_WORKER_STARTED = False
_MODULE_WORKER_LOCK = threading.Lock()


def _module_worker():
    while True:
        fn, name = _MODULE_QUEUE.get()
        try:
            with _TASKS_LOCK:   # queued -> running as it actually starts
                j = MODULE_JOBS.get(name)
                if j is not None:
                    j["status"] = "running"
            fn()
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Module job worker error for {name}: {e}")
            with _TASKS_LOCK:
                MODULE_JOBS[name] = {"status": "error", "output": str(e)}
        finally:
            _MODULE_QUEUE.task_done()


def _ensure_module_worker():
    global _MODULE_WORKER_STARTED
    with _MODULE_WORKER_LOCK:
        if not _MODULE_WORKER_STARTED:
            threading.Thread(target=_module_worker, daemon=True).start()
            _MODULE_WORKER_STARTED = True


def _enqueue_module_job(name, fn):
    """Mark `name` queued and add its work to the serial queue. Refuses only if
    THIS plugin already has a pending/running op (avoids duplicate). Returns the
    queue position (0 = will run now / next)."""
    _ensure_module_worker()
    with _TASKS_LOCK:
        cur = MODULE_JOBS.get(name, {}).get("status")
        if cur in ("queued", "running"):
            return None   # already pending for this plugin
        MODULE_JOBS[name] = {"status": "queued", "output": ""}
    pos = _MODULE_QUEUE.qsize()
    _MODULE_QUEUE.put((fn, name))
    return pos


def _run_module_job(name, action):
    freed = 0
    ok, out = False, ""
    # Hold the global lock for the whole job so concurrent pip/uv calls can't
    # corrupt the shared env; ALWAYS land on a terminal status (try/finally) so a
    # crash can't leave the job stuck "running" (which would freeze the UI button).
    with _MODULE_JOB_LOCK:
        # Stream pip/uv output into the job so the UI shows live progress.
        from core.module_manager import set_progress_callback
        def _prog(line):
            with _TASKS_LOCK:
                j = MODULE_JOBS.get(name)
                if j is not None:
                    j["line"] = line[:200]
        set_progress_callback(_prog)
        try:
            from core.module_manager import install_module, upgrade_module
            if action == "uninstall":
                from core.optional_modules import uninstall_plugin
                ok, out, freed = uninstall_plugin(name)
            else:
                fn = {"install": install_module, "upgrade": upgrade_module}[action]
                ok, out = fn(name)
            if ok and action == "install":
                # Best-effort warm the default model; a just-installed package may
                # not import until restart, so any failure here is non-fatal.
                try:
                    from core.optional_modules import download_plugin_model
                    download_plugin_model(name)
                except Exception:  # noqa: BLE001
                    pass
            if ok:
                # (module_manager.install/uninstall already invalidated import
                # caches + cleared the size cache, so the next status/usage call
                # sees the change — no restart needed for detection.)
                from core.log_config import system_event
                from core.model_store import human_size
                extra = f" | freed {human_size(freed)}" if (action == "uninstall" and freed) else ""
                system_event(f"Plugin {action}: {name}{extra}")
        except Exception as e:  # noqa: BLE001 — never leave the job stuck running
            ok, out = False, f"{type(e).__name__}: {e}"
            app_logger.error(f"Plugin {action} job crashed for {name}: {e}")
        finally:
            set_progress_callback(None)
            with _TASKS_LOCK:
                MODULE_JOBS[name] = {"status": "done" if ok else "error",
                                     "output": out, "freed_bytes": freed}


def _run_model_job(name, model_id):
    """Persist the chosen model id, then download+warm it (heavy/blocking)."""
    ok = False
    with _MODULE_JOB_LOCK:
        try:
            from core.optional_modules import set_plugin_model, download_plugin_model
            set_plugin_model(name, model_id)
            ok = download_plugin_model(name, model_id)
        except Exception as e:  # noqa: BLE001
            ok = False
            app_logger.error(f"Model download job crashed for {name}/{model_id}: {e}")
        finally:
            with _TASKS_LOCK:
                MODULE_JOBS[name] = {"status": "done" if ok else "error", "output": ""}


@app.post("/api/modules/{action}")
async def module_action(action: str, payload: dict):
    _block_in_server_mode()
    if action not in ("install", "uninstall", "upgrade"):
        raise HTTPException(400, "action must be install|uninstall|upgrade")
    name = payload.get("name")
    # Query the registry LIVE (not the import-time MODULE_SPECS snapshot) so a
    # plugin downloaded from the market this session is recognized.
    from core import plugins_registry
    if plugins_registry.get(name) is None:
        raise HTTPException(404, f"Unknown module: {name}")
    pos = _enqueue_module_job(name, lambda: _run_module_job(name, action))
    if pos is None:
        raise HTTPException(409, f"'{name}' already has a pending operation.")
    return {"started": True, "queue_position": pos}


@app.post("/api/modules/model")
async def module_set_model(payload: dict):
    """Persist a plugin's model choice and download+warm it in the background.
    Poll GET /api/modules/status?name=... for completion (same job channel)."""
    _block_in_server_mode()
    name = payload.get("name")
    model_id = payload.get("model_id")
    if not name or not model_id:
        raise HTTPException(400, "name and model_id are required")
    # The worker (_run_model_job) persists the choice + downloads, so a duplicate
    # request can't repoint the model and a bad id surfaces as a job error.
    pos = _enqueue_module_job(name, lambda: _run_model_job(name, model_id))
    if pos is None:
        raise HTTPException(409, f"'{name}' already has a pending operation.")
    return {"started": True, "queue_position": pos}


@app.get("/api/modules/status")
def module_status_endpoint(name: str):
    with _TASKS_LOCK:
        job = dict(MODULE_JOBS.get(name, {"status": "idle", "output": ""}))
    # current availability (changes after a restart, but report live anyway)
    avail = {m["name"]: m["available"] for m in module_status()}
    job["available"] = avail.get(name, False)
    return job


@app.get("/api/modules/usage")
def modules_usage():
    """Per-plugin library (pip deps) + model disk volumes, for the plugin cards'
    space summary. Computed lazily (not in bootstrap) + cached so it never slows
    page load (lib-size stat-walk is a few seconds the first time)."""
    from core.optional_modules import plugin_space
    out = {}
    for m in module_status():
        s = plugin_space(m["name"])
        out[m["name"]] = {
            "lib_human": s["lib_human"], "model_human": s["model_human"],
            "lib_bytes": s["lib_bytes"], "model_bytes": s["model_bytes"],
            "shared": s["shared"],
        }
    return {"usage": out}


@app.get("/api/modules/update-check")
def module_update_check(name: str):
    """Report whether a newer version of the module's package exists on PyPI.

    Reports only — the upgrade itself is the user-confirmed
    POST /api/modules/upgrade. Returns {} when there's nothing to report.
    """
    _block_in_server_mode()
    from core.module_manager import check_module_update
    return check_module_update(name) or {}


@app.get("/api/modules/market")
def modules_market():
    """Remote plugins available to download (not already present locally)."""
    _block_in_server_mode()
    from core import plugins_registry
    return {"plugins": plugins_registry.remote_available()}


@app.post("/api/modules/download")
def modules_download(payload: dict):
    """Download a self-contained plugin from the remote market into the writable
    plugins dir. Only the KEY is taken from the client — the download URL is
    resolved server-side from the trusted market index (so a request can't point
    the download at an arbitrary URL). Dependency install is the normal step after."""
    _block_in_server_mode()
    from core import plugins_registry
    key = payload.get("key")
    if not key:
        raise HTTPException(400, "key is required")
    if _module_busy():
        raise HTTPException(409, "Another plugin operation is in progress. Please wait.")
    ok, msg = plugins_registry.download_remote_plugin(key)   # url resolved from index
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


# --------------------------------------------------------------------------- #
# Real-time voice translation (Gemini 3.5 Live Translate, proxied so the
# Google key stays server-side). Browser streams 16k PCM, gets back 24k PCM
# audio + input/output transcripts.
# --------------------------------------------------------------------------- #
GEMINI_LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
                   "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
GEMINI_LIVE_MODEL = "models/gemini-3.5-live-translate-preview"


@app.websocket("/ws/live-translate")
async def live_translate(ws: WebSocket):
    # Reject cross-origin browser connections (CSWSH): this endpoint spends the
    # server-side Google key, so only same-origin pages may open it. Non-browser
    # clients send no Origin and aren't subject to CSRF.
    origin = ws.headers.get("origin")
    host = ws.headers.get("host", "")          # includes the port, e.g. 127.0.0.1:8080
    port = host.split(":", 1)[1] if ":" in host else ""
    allowed = {f"http://{host}", f"https://{host}"}
    # Loopback aliases on the SAME port (the Host header may differ from how the
    # browser addressed it, e.g. localhost vs 127.0.0.1).
    for h in ("localhost", "127.0.0.1"):
        allowed.add(f"http://{h}:{port}" if port else f"http://{h}")
        allowed.add(f"https://{h}:{port}" if port else f"https://{h}")
    if origin is not None and origin not in allowed:
        await ws.close(code=1008)
        return
    await ws.accept()
    # Concurrency cap — refuse if too many live sessions already spend the key.
    global _live_ws_count
    with _live_ws_count_lock:
        if _live_ws_count >= _LIVE_WS_MAX:
            await ws.send_json({"type": "error",
                                "message": f"实时语音并发已满（最多 {_LIVE_WS_MAX} 路），请稍后再试。"})
            await ws.close(code=1013)
            return
        _live_ws_count += 1
    target = ws.query_params.get("target", "zh")
    key = load_api_key_for_model("(Google) Live Translate")  # provider "Google"
    if not key:
        await ws.send_json({"type": "error", "message": "Google API key not set (Settings)."})
        await ws.close()
        with _live_ws_count_lock:
            _live_ws_count -= 1
        return
    try:
        import websockets
    except Exception:
        await ws.send_json({"type": "error", "message": "websockets package missing."})
        await ws.close()
        with _live_ws_count_lock:
            _live_ws_count -= 1
        return

    try:
        async with websockets.connect(f"{GEMINI_LIVE_URL}?key={key}", max_size=None) as gws:
            await gws.send(json.dumps({"setup": {
                "model": GEMINI_LIVE_MODEL,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "translationConfig": {"targetLanguageCode": target, "echoTargetLanguage": True},
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }}))

            async def client_to_gemini():
                try:
                    while True:
                        d = json.loads(await ws.receive_text())
                        if "audio" in d:
                            await gws.send(json.dumps({"realtimeInput": {
                                "audio": {"data": d["audio"], "mimeType": "audio/pcm;rate=16000"}}}))
                        elif d.get("end"):
                            await gws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                except (WebSocketDisconnect, RuntimeError):
                    pass

            async def gemini_to_client():
                async for raw in gws:
                    await ws.send_text(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))

            # Hard cap the session length so a forgotten tab can't stream (and
            # spend the key) indefinitely.
            await asyncio.wait_for(
                asyncio.gather(client_to_gemini(), gemini_to_client()),
                timeout=_LIVE_WS_MAX_SECONDS)
    except asyncio.TimeoutError:
        try:
            await ws.send_json({"type": "error", "message": "实时语音会话已达最长时长，请重新开始。"})
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "message": str(e)[:300]})
        except Exception:
            pass
    finally:
        with _live_ws_count_lock:
            _live_ws_count -= 1
        try:
            await ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Real-time voice translation — LOCAL path (SenseVoice STT + LLM translate).
# The client does VAD and POSTs one complete utterance (16 kHz mono PCM16,
# base64); we recognize it locally then translate with the active model. No
# Google key needed; requires the Video/Audio plugin (funasr).
# --------------------------------------------------------------------------- #
_STT_LOCK = threading.Lock()  # funasr is not thread-safe — serialize recognition

# Google Live WS guards: this endpoint spends the server-side Google key, so cap
# concurrent sessions and the max session length (esp. for LAN deploys where one
# user could open many tabs and stream forever).
_LIVE_WS_MAX = 3
_LIVE_WS_MAX_SECONDS = 1800   # 30 min hard cap per session
_live_ws_count = 0
_live_ws_count_lock = threading.Lock()


@app.post("/api/live-preload")
async def live_preload(payload: dict = None):
    """Load the STT model up front so the first utterance isn't blocked on a
    multi-second model load. scope='live' (default) preloads the live-voice
    model; scope='quick' preloads the Quick-Translate voice model (they can be
    different). Real-time voice needs only an STT engine — NO ffmpeg."""
    if not realtime_voice_available():
        raise HTTPException(400, "实时语音需要语音(STT)插件。")
    from core.pipelines.video_translation_pipeline import (
        preload_recognizer, recognizer_ready,
        get_selected_live_stt_model, get_selected_quick_stt_model)
    scope = (payload or {}).get("scope", "live")
    model_id = (get_selected_quick_stt_model() if scope == "quick"
                else get_selected_live_stt_model())
    if scope == "live" and recognizer_ready():
        return {"ready": True}
    loop = asyncio.get_event_loop()
    ready = await loop.run_in_executor(None, lambda: preload_recognizer(model_id))
    return {"ready": bool(ready)}


@app.post("/api/live-recognize")
async def live_recognize(payload: dict, request: Request):
    """Step 1 of local live voice: recognize one utterance -> source text.
    Split from translation so the UI can show the source line immediately."""
    if not realtime_voice_available():
        raise HTTPException(400, "实时语音需要语音(STT)插件。")
    import base64
    try:
        pcm = base64.b64decode(payload.get("audio_b64", ""))
    except Exception:
        raise HTTPException(400, "Bad audio payload")
    if not pcm:
        return {"source": "", "detected": ""}
    # Bound STT cost: cap to the most recent ~32s (matches the client's max
    # utterance) so a runaway/huge body can't blow up CPU/memory.
    if len(pcm) > _MAX_LIVE_AUDIO_BYTES:
        pcm = pcm[-_MAX_LIVE_AUDIO_BYTES:]
    is_final = bool(payload.get("final", False))
    from core.pipelines.video_translation_pipeline import recognize_utterance
    loop = asyncio.get_event_loop()

    def _recognize():
        # _STT_LOCK serializes recognition (funasr isn't thread-safe). Under LAN
        # contention, DROP partials (latest-wins — the next partial retries) but
        # let finals wait, so concurrent speakers can't pile up an unbounded queue.
        timeout = 8.0 if is_final else 0.25
        if not _STT_LOCK.acquire(timeout=timeout):
            return None
        try:
            text, det = recognize_utterance(pcm, sample_rate=16000)
            return text, det
        finally:
            _STT_LOCK.release()
    res = await loop.run_in_executor(None, _recognize)
    if res is None:
        return {"source": "", "detected": "", "busy": True}
    source, detected = res
    return {"source": source or "", "detected": detected or ""}


@app.post("/api/live-translate-text")
async def live_translate_text(payload: dict):
    """Step 2 of local live voice: translate a recognized line. Model/online are
    taken from the ACTIVE interface (no Settings checkbox)."""
    source = _capped_text(payload, "source")
    if not source:
        return {"translated": ""}
    dst_lang = payload.get("dst_lang", "en")
    src_code = payload.get("src_lang") or "auto"
    context = _capped_text(payload, "context")
    cfg = backend.read_config()
    use_online = bool(cfg.get("default_online", True))
    model = backend.get_active_model(use_online=use_online)
    api_key = (load_api_key_for_model(model)
               or os.environ.get("LINGUAHARU_API_KEY", "")) if use_online else ""
    from core.llm.llm_wrapper import translate_text_simple
    loop = asyncio.get_event_loop()

    def _translate():
        return translate_text_simple(source, src_code, dst_lang, model, use_online,
                                     api_key, context=context)
    try:
        translated, ok, usage = await loop.run_in_executor(None, _translate)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Translate failed: {e}")
    tokens = int((usage or {}).get("total_tokens", 0) or 0)
    return {"translated": translated if ok else "", "tokens": tokens}


@app.get("/api/cache/stats")
def cache_stats():
    """Translation-memory size (rows + bytes) for the Settings panel."""
    from core.engine.translation_cache import stats
    rows, size = stats()
    return {"rows": rows, "bytes": size}


@app.post("/api/cache/clear")
def cache_clear():
    """Wipe the translation memory (privacy / reset)."""
    _block_in_server_mode()
    from core.engine.translation_cache import clear
    return {"ok": clear()}


@app.post("/api/inpaint-download")
async def inpaint_download():
    """Download the optional LaMa inpainting model (high-quality image text
    erasure). Runs in a worker thread; returns when ready."""
    _block_in_server_mode()
    from core.pipelines.lama_inpaint import download_lama
    loop = asyncio.get_event_loop()
    try:
        path = await loop.run_in_executor(None, download_lama)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"LaMa download failed: {e}")
    return {"ok": True, "path": str(path)}


@app.post("/api/ensure-web-vad")
def ensure_web_vad():
    """Download the heavy Web neural-VAD assets (onnxruntime WASM + Silero ONNX)
    into static/vad on first use, so they're self-hosted (same-origin) and the
    page's COEP:require-corp doesn't block them. Small JS loaders are shipped."""
    import urllib.request
    vad_dir = VAD_DIR
    os.makedirs(vad_dir, exist_ok=True)
    assets = {
        "ort-wasm-simd-threaded.wasm":
            "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.19.2/dist/ort-wasm-simd-threaded.wasm",
        "silero_vad_legacy.onnx":
            "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.22/dist/silero_vad_legacy.onnx",
    }
    missing = []
    for name, url in assets.items():
        p = os.path.join(vad_dir, name)
        if not (os.path.exists(p) and os.path.getsize(p) > 1000):
            try:
                urllib.request.urlretrieve(url, p)
            except Exception:  # noqa: BLE001
                missing.append(name)
    return {"ok": not missing, "missing": missing}


@app.post("/api/live-translate-stream")
def live_translate_stream(payload: dict):
    """Stream a live-caption translation token-by-token (SSE). Each event is a
    JSON-encoded cumulative string; ends with [DONE]. Online-only; the generator
    falls back to a single chunk offline/on failure."""
    import json as _json
    from fastapi.responses import StreamingResponse
    source = _capped_text(payload, "source")
    dst_lang = payload.get("dst_lang", "en")
    src_code = payload.get("src_lang") or "auto"
    context = _capped_text(payload, "context")
    cfg = backend.read_config()
    use_online = bool(cfg.get("default_online", True))
    model = backend.get_active_model(use_online=use_online)
    api_key = (load_api_key_for_model(model)
               or os.environ.get("LINGUAHARU_API_KEY", "")) if use_online else ""

    def gen():
        if source:
            from core.llm.llm_wrapper import translate_text_simple_stream
            sink = {}
            try:
                for partial in translate_text_simple_stream(
                        source, src_code, dst_lang, model, use_online, api_key,
                        usage_sink=sink, context=context):
                    yield f"data: {_json.dumps(partial)}\n\n"
            except Exception:  # noqa: BLE001
                yield f"data: {_json.dumps('')}\n\n"
            if sink.get("total_tokens"):   # tell the client this line's token cost
                yield f"data: {_json.dumps({'__usage__': sink['total_tokens']})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/live-save-history")
def live_save_history(payload: dict, request: Request):
    """Save a finished real-time-voice session (source + translation) to the
    per-session history. Called by the frontend when a live session stops."""
    if server_mode_on():               # no history on public/shared deploys
        return {"saved": False}
    src = payload.get("source_lines") or []
    dst = payload.get("translated_lines") or []
    if not src and not dst:
        return {"saved": False}
    _, result_dir, _ = sessions.session_paths(request.state.session_id)
    log_dir = history_log_dir(request.state.session_id)
    cfg = backend.read_config()
    use_online = bool(cfg.get("default_online", True))
    model = backend.get_active_model(use_online=use_online)
    tokens = int(payload.get("tokens", 0) or 0)
    # Estimate cost from the session's tokens (output-heavy, but we only have the
    # total — split is unknown, so attribute it all to completion = upper bound).
    cost_amount = cost_currency = cost_symbol = None
    if use_online and tokens > 0:
        try:
            from core.pricing import estimate_cost
            amt, sym, ccy = estimate_cost(model, 0, tokens, payload.get("ui_lang", "en"))
            cost_amount, cost_symbol, cost_currency = round(amt, 4), sym, ccy
        except Exception:  # noqa: BLE001
            pass
    from core.translation_history import save_live_session
    rec = save_live_session(
        src, dst, payload.get("src_display", "Auto"),
        payload.get("dst_display", ""), model, use_online, result_dir, log_dir,
        total_tokens=tokens, cost_amount=cost_amount, cost_currency=cost_currency)
    return {"saved": bool(rec), "tokens": tokens,
            "cost": ({"amount": cost_amount, "symbol": cost_symbol, "currency": cost_currency}
                     if cost_amount is not None else None)}


# --- quick (short-text) translate, Google-Translate-style -------------------
def _quick_store_dir(request):
    """Per-session history dir so users on a shared/LAN deploy never see each
    other's quick-translate history (falls back to the global dir if no session)."""
    try:
        _, _, log_dir = sessions.session_paths(request.state.session_id)
        return log_dir
    except Exception:  # noqa: BLE001
        return None


@app.post("/api/quick-translate")
async def quick_translate_api(payload: dict, request: Request):
    """Translate a short text via the active interface; record recent history
    (scoped to the caller's session). Voice input goes through /api/live-recognize."""
    text = _capped_text(payload, "text")
    if not text:
        return {"translated": "", "history": []}
    src_lang = payload.get("src_lang") or "auto"
    dst_lang = payload.get("dst_lang", "en")
    context = (payload.get("context") or "").strip()[:300]
    from core import quick_translate
    loop = asyncio.get_event_loop()
    try:
        translated, ok = await loop.run_in_executor(
            None, quick_translate.translate, text, src_lang, dst_lang, context)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Translate failed: {e}")
    history = []
    # Don't persist history on a public/shared deploy (cross-user privacy).
    if ok and translated and not server_mode_on():
        history = quick_translate.add_history(text, translated, src_lang, dst_lang,
                                              store_dir=_quick_store_dir(request))
    return {"translated": translated, "history": history}


@app.get("/api/quick-history")
def quick_history_api(request: Request):
    if server_mode_on():
        return {"history": []}
    from core import quick_translate
    return {"history": quick_translate.get_history(store_dir=_quick_store_dir(request))}


@app.post("/api/quick-history/clear")
def quick_history_clear_api(request: Request):
    _block_in_server_mode()
    from core import quick_translate
    return {"history": quick_translate.clear_history(store_dir=_quick_store_dir(request))}


@app.post("/api/quick-recognize")
async def quick_recognize_api(payload: dict):
    """Quick-Translate voice input: recognize one utterance using the plugin's
    own STT model (quick_stt_model)."""
    from core.optional_modules import realtime_voice_available
    if not realtime_voice_available():
        raise HTTPException(400, "需要「翻译语音输入」插件(STT)")
    import base64
    try:
        pcm = base64.b64decode(payload.get("audio_b64", ""))
    except Exception:
        raise HTTPException(400, "Bad audio payload")
    if not pcm:
        return {"source": "", "detected": ""}
    if len(pcm) > _MAX_LIVE_AUDIO_BYTES:
        pcm = pcm[-_MAX_LIVE_AUDIO_BYTES:]
    from core.pipelines.video_translation_pipeline import (
        recognize_utterance, get_selected_quick_stt_model)
    loop = asyncio.get_event_loop()

    def _recognize():
        with _STT_LOCK:
            return recognize_utterance(pcm, sample_rate=16000,
                                       model_id=get_selected_quick_stt_model())
    source, detected = await loop.run_in_executor(None, _recognize)
    return {"source": source or "", "detected": detected or ""}


@app.post("/api/tts")
async def tts_api(payload: dict):
    """Read-aloud (TTS) for the Quick-Translate output. Returns MP3 bytes."""
    from core.optional_modules import tts_available
    if not tts_available():
        raise HTTPException(400, "需要「翻译语音输入」插件(edge-tts)")
    text = _capped_text(payload, "text")
    if not text:
        raise HTTPException(400, "Empty text")
    lang = payload.get("lang", "en")
    from core import tts
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, tts.synthesize, text, lang)
    if not audio:
        raise HTTPException(500, "TTS failed (network?)")
    from fastapi import Response
    return Response(content=audio, media_type="audio/mpeg")


# --------------------------------------------------------------------------- #
# Update check (GitHub Releases, China-friendly mirrors). Cached 1h so it never
# blocks page loads repeatedly; the frontend shows a dismissible banner.
# --------------------------------------------------------------------------- #
_UPDATE_CACHE = {"ts": 0.0, "data": None}


@app.get("/api/update-check")
def update_check():
    import time as _time
    from core.updater import check_for_update
    now = _time.time()
    if now - _UPDATE_CACHE["ts"] > 3600 or _UPDATE_CACHE["data"] is None:
        _UPDATE_CACHE["data"] = check_for_update()
        _UPDATE_CACHE["ts"] = now
    return _UPDATE_CACHE["data"] or {"update": False}


_SELF_UPDATE = {"status": "idle", "progress": 0.0, "stage": "", "message": ""}
_SELF_UPDATE_LOCK = threading.Lock()


def _run_self_update(asset_url, sha256):
    from core.updater import download_and_apply

    def cb(frac, stage=""):
        _SELF_UPDATE.update(progress=round(float(frac), 3), stage=stage)
    try:
        ok, msg = download_and_apply(asset_url, sha256, cb)
        _SELF_UPDATE.update(status="done" if ok else "error", message=msg,
                            progress=1.0 if ok else _SELF_UPDATE["progress"])
    except Exception as e:  # noqa: BLE001
        _SELF_UPDATE.update(status="error", message=str(e))


@app.post("/api/self-update")
def self_update(payload: dict = None):
    """Download + apply the new portable build in place (keeps python/, models/,
    data/, user settings). Portable-only; poll /api/self-update/status."""
    _block_in_server_mode()
    from core.updater import portable_root, check_for_update
    if not portable_root():
        raise HTTPException(400, "Smart update is only available in the portable build.")
    info = check_for_update() or {}
    asset, sha = info.get("asset_url"), info.get("asset_sha256")
    if not asset or not sha:
        raise HTTPException(400, "No verified package available for this build.")
    # Atomic check-and-set so two concurrent requests can't both start an update
    # (each would delete+replace the program dir).
    with _SELF_UPDATE_LOCK:
        if _SELF_UPDATE["status"] == "running":
            raise HTTPException(409, "An update is already in progress.")
        _SELF_UPDATE.update(status="running", progress=0.0, stage="starting", message="")
    threading.Thread(target=_run_self_update, args=(asset, sha), daemon=True).start()
    return {"started": True}


@app.get("/api/self-update/status")
def self_update_status():
    return dict(_SELF_UPDATE)


class _NoCacheStatic(StaticFiles):
    """StaticFiles that asks browsers to revalidate every time, so UI edits to
    style.css / app.js show up on reload instead of serving a stale cached copy
    (browsers cache CSS heuristically when no Cache-Control is sent)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


# Mount the writable VAD dir at /static/vad BEFORE /static so it takes precedence
# (Starlette matches mounts in order). Frozen builds serve the seeded/downloaded
# neural-VAD assets from data/web_vad instead of the read-only bundle.
if os.path.isdir(VAD_DIR):
    app.mount("/static/vad", _NoCacheStatic(directory=VAD_DIR), name="static-vad")
app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")

# Serve assets/ (file-type SVG icons, images) so the Web UI can reuse the same
# icon set as the Qt app. core.paths.ASSETS_DIR is __file__-anchored, so it
# resolves under _MEIPASS in a frozen build too.
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")


def server_host():
    """Bind address: 0.0.0.0 (reachable from other devices) in LAN mode or
    server/deploy mode, otherwise loopback-only."""
    external = backend.get_config("lan_mode", False) or server_mode_on()
    return "0.0.0.0" if external else "127.0.0.1"


def find_free_port(preferred, host, tries=50):
    """First free port at/after `preferred` (a busy 8080 rolls to 8081, ...).

    Probes with a strict bind (no SO_REUSEADDR): on Windows SO_REUSEADDR would
    let the probe bind an already-listened port and falsely report it free.
    Also skips Windows reserved/excluded ranges (bind raises PermissionError)."""
    import socket
    for p in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return preferred  # let uvicorn surface the bind error if all are taken


def server_port(host):
    """Port to listen on. A deploy platform assigns PORT and it must be used
    verbatim; locally we auto-roll past an already-occupied port."""
    if os.environ.get("PORT"):
        return int(os.environ["PORT"])
    return find_free_port(8080, host)


def _open_browser_when_ready(url, host, port):
    """Open the default browser once the server is actually accepting connections.
    Local desktop use only — skipped on headless/deploy (RENDER / server_mode) and
    when LINGUAHARU_NO_BROWSER is set."""
    import socket
    import time
    import threading
    import webbrowser

    def _wait_and_open():
        probe = "127.0.0.1" if host in ("0.0.0.0", "") else host
        for _ in range(120):   # up to ~60s while the ML stack imports
            try:
                with socket.create_connection((probe, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.5)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — never let browser-open break the server
            pass

    threading.Thread(target=_wait_and_open, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    host = server_host()
    port = server_port(host)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    print(f"LinguaHaru Web → {url}")
    # Auto-open the browser for a local desktop launch (double-click Start-Web.bat),
    # so the user doesn't have to type the address. Skip on deploy/headless.
    if not server_mode_on() and not os.environ.get("LINGUAHARU_NO_BROWSER"):
        _open_browser_when_ready(url, host, port)
    uvicorn.run(app, host=host, port=port)
