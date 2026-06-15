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
import hashlib
import hmac
import secrets

from fastapi import (
    FastAPI, UploadFile, Form, HTTPException, WebSocket, WebSocketDisconnect,
    Request)
from fastapi.responses import (
    FileResponse, StreamingResponse, HTMLResponse)
from fastapi.staticfiles import StaticFiles

from core import backend
from webapp import sessions
from core.api_keys import (
    load_api_key_for_model, save_api_key_for_model, provider_of)
from core.languages_config import LABEL_TRANSLATIONS, LANGUAGE_MAP
from core.optional_modules import (
    module_status, video_translation_available)
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
# Uploads must live OUTSIDE the translation temp dir: DocumentTranslator.process()
# wipes temp/ on a fresh run, which would delete the file being translated.
UPLOAD_DIR = os.path.join(backend.REPO_ROOT, "data", "web_uploads")

app = FastAPI(title="LinguaHaru Web")


def server_mode_on():
    """Public-deploy mode: hide the key/model/admin UI, use the server's own
    key, and bind externally. On via the ``server_mode`` config flag, or
    automatically on Render (the ``RENDER`` env var is always set there)."""
    return bool(backend.get_config("server_mode", False)) or bool(os.environ.get("RENDER"))


# Carries the per-request admin token (set by the middleware) so the sync admin
# endpoints can check it without each taking a `request` parameter.
_admin_token = contextvars.ContextVar("admin_token", default="")


_PBKDF2_ITERS = 200_000


def _hash_pw(pw):
    """Salted PBKDF2-HMAC-SHA256 of a password (stdlib, no extra deps). We store
    only this hash — never plaintext, since system_config.json is git-tracked.
    Format: pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                             bytes.fromhex(salt), _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt}${dk.hex()}"


def _verify_pw(pw, stored):
    """Constant-time check of a password against a stored PBKDF2 hash."""
    try:
        algo, iters, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:
        return False


def _block_in_server_mode():
    """Guard admin-only endpoints (changing the server's model/key, RPM, modules,
    interfaces). Always blocked in public server mode. In LAN mode, if an admin
    password is configured, callers must supply it (X-Admin-Token header) — so an
    untrusted LAN user can't change keys/models/config/modules."""
    if server_mode_on():
        raise HTTPException(403, "Disabled in server mode")
    pw_hash = str(backend.get_config("lan_admin_password_hash", "") or "")
    if pw_hash and backend.get_config("lan_mode", False):
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
        "sensevoice_codes": sorted(SENSEVOICE_SUPPORTED_CODES),
        "language_map": LANGUAGE_MAP,
        "modules": module_status(),
        "server_mode": server_mode_on(),
        "local_live_available": video_translation_available(),
        "config": {
            "default_online": online,
            "default_online_model": config.get("default_online_model", ""),
            "default_src_lang": config.get("default_src_lang", "English"),
            "default_dst_lang": config.get("default_dst_lang", "中文"),
            "default_glossary": config.get("default_glossary", "Default"),
            "stt_model": get_selected_stt_model(),
            "translate_subtitles": config.get("translate_subtitles", True),
            "max_retries": config.get("max_retries", 4),
            # Show the RPM that's actually in effect: an explicit user value, or
            # the safety-net default when unset (so the UI isn't misleading).
            "rpm_limit": config.get("rpm_limit", _DEFAULT_RPM),
            "auto_extract_glossary": config.get("auto_extract_glossary", False),
            "lan_mode": config.get("lan_mode", False),
            "has_lan_admin": bool(config.get("lan_admin_password_hash")),  # never expose the value
            "default_thread_count_online": config.get("default_thread_count_online", 8),
            "default_thread_count_offline": config.get("default_thread_count_offline", 4),
            "thread_count": backend.thread_count_for_mode(
                online, config.get("default_online_model")),
        },
        "labels": LABEL_TRANSLATIONS,
    }


@app.post("/api/config")
async def update_config(payload: dict):
    """Persist arbitrary settings keys (whitelisted)."""
    _block_in_server_mode()
    allowed = {"default_online", "default_online_model", "default_src_lang",
               "default_dst_lang", "default_glossary", "stt_model",
               "translate_subtitles", "max_retries", "rpm_limit",
               "auto_extract_glossary", "lan_mode",
               "default_thread_count_online", "default_thread_count_offline",
               "max_api_concurrency"}
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
    import pandas as pd
    path = backend.glossary_path(name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"Glossary not found: {name}")
    for enc in ("utf-8-sig", "utf-8", "gbk", "shift-jis"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype=str).fillna("")
            return {"columns": list(df.columns), "rows": df.values.tolist()}
        except UnicodeDecodeError:
            continue
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Failed to load glossary: {e}")
    raise HTTPException(500, "Failed to decode glossary file")


@app.post("/api/glossary")
async def save_glossary(payload: dict):
    import pandas as pd
    name = payload.get("name")
    path = backend.glossary_path(name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, f"Glossary not found: {name}")
    df = pd.DataFrame(payload.get("rows", []), columns=payload.get("columns", []))
    df = df[~(df.astype(str).apply(lambda r: "".join(r).strip() == "", axis=1))]
    if len(df) == 0 and os.path.getsize(path) > 0:
        raise HTTPException(400, "Refused: empty table over a non-empty glossary.")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return {"ok": True, "count": len(df)}


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
def _translate_one(task_id, session_id, file_path, model, use_online, src_lang,
                   dst_lang, glossary_name, bilingual_flags, on_progress):
    """Translate a single file; returns its output path. Raises on failure.

    Paths are scoped to ``session_id`` so concurrent users never collide, and a
    stop is honored either per-task (this run) or per-session (the caller hit
    Stop / disconnected)."""
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
    temp_dir, result_dir, log_dir = sessions.session_paths(session_id)
    # Per-task subdir so two same-named files in ONE session don't collide
    # (DocumentTranslator.file_dir is derived from basename). Cross-session was
    # already isolated; this closes the same-session same-name case.
    temp_dir = os.path.join(temp_dir, task_id)
    result_dir = os.path.join(result_dir, task_id)
    log_dir = os.path.join(log_dir, task_id)
    for _d in (temp_dir, result_dir, log_dir):
        os.makedirs(_d, exist_ok=True)
    config = backend.read_config()

    from core.log_config import file_logger
    file_logger.create_file_log(os.path.basename(file_path), log_dir=log_dir)

    translator = translator_class(
        file_path, model, use_online, api_key, src_code, dst_code, False,
        max_token=config.get("max_token", 768),
        max_retries=backend.max_retries_for_model(model if use_online else None),
        thread_count=backend.thread_count_for_mode(use_online, model),
        glossary_path=gpath, temp_dir=temp_dir, result_dir=result_dir,
        session_lang="en", log_dir=log_dir,
    )

    def check_stop():
        # Task-scoped only: stopping one task never aborts this session's others.
        with _TASKS_LOCK:
            if TASKS.get(task_id, {}).get("stop"):
                raise RuntimeError("__stopped__")
    translator.check_stop_requested = check_stop
    output_path, _missing = translator.process(
        stem, ext, progress_callback=lambda v, desc=None: (check_stop(), on_progress(v, desc)))
    return output_path


def _run_translation(task_id, session_id, file_paths, model, use_online,
                     src_lang, dst_lang, glossary_name, bilingual_flags):
    """Background worker: translate one or more files; zip when more than one."""
    def set_state(**kw):
        with _TASKS_LOCK:
            TASKS[task_id].update(kw)
            if kw.get("status") in ("done", "error", "stopped"):
                TASKS[task_id]["ended_at"] = time.time()

    total = len(file_paths)
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
                    dst_lang, glossary_name, bilingual_flags, on_progress))
                file_results.append((name, "success", ""))
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
            set_state(status="done", progress=1.0, desc="Translation completed",
                      output=outputs[0])
        else:
            zip_path = backend.zip_results(outputs, file_results)
            ok = sum(1 for _, s, _ in file_results if s == "success")
            set_state(status="done", progress=1.0,
                      desc=f"Translation completed ({ok}/{total})", output=zip_path)
    except RuntimeError as e:
        if "__stopped__" in str(e):
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
    for f in files:
        dest = os.path.join(upload_dir, os.path.basename(f.filename or "upload"))
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        dests.append(dest)

    with _TASKS_LOCK:
        TASKS[task_id] = {"progress": 0.0, "desc": "Queued...",
                          "status": "running", "output": None, "error": None,
                          "stop": False, "session_id": session_id}
    flags = {k: bilingual for k in (
        "excel_bilingual_mode", "word_bilingual_mode", "pdf_bilingual_mode",
        "subtitle_bilingual_mode", "txt_bilingual_mode", "md_bilingual_mode",
        "epub_bilingual_mode", "html_bilingual_mode")}
    threading.Thread(target=_run_translation, args=(
        task_id, session_id, dests, model, use_online, src_lang, dst_lang,
        glossary, flags), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
def progress(task_id: str, request: Request):
    with _TASKS_LOCK:
        owner = TASKS.get(task_id, {}).get("session_id")
    if owner is None or owner != request.state.session_id:
        raise HTTPException(404, "Unknown task")

    def stream():
        last = None
        while True:
            with _TASKS_LOCK:
                state = dict(TASKS.get(task_id, {}))
            snapshot = (round(state.get("progress", 0), 4), state.get("desc"),
                        state.get("status"))
            if snapshot != last:
                last = snapshot
                yield f"data: {json.dumps({k: state.get(k) for k in ('progress','desc','status','error')})}\n\n"
            if state.get("status") in ("done", "error", "stopped"):
                break
            import time
            time.sleep(0.2)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/stop/{task_id}")
def stop(task_id: str, request: Request):
    sid = request.state.session_id
    with _TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is not None and task.get("session_id") == sid:
            task["stop"] = True   # task-scoped; other tasks in this session keep running
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
            sort_by: str = "start_time", desc: bool = True):
    from core.translation_history import TranslationHistoryManager
    _, _, log_dir = sessions.session_paths(request.state.session_id)
    h = TranslationHistoryManager(log_dir=log_dir)
    records = h.get_all_records(limit=limit, file_type=(file_type or None),
                                sort_by=sort_by, descending=desc)
    return {"records": records, "file_types": h.file_types()}


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
    EXPORTS[name] = path
    return {"ok": True, "filename": os.path.basename(path)}


EXPORTS = {}  # doc_name -> exported file path


@app.get("/api/proofread/download")
def proofread_download(name: str, request: Request):
    if sessions.proofread_doc_dir(name, request.state.session_id) is None:
        raise HTTPException(404, "Export not ready")
    path = EXPORTS.get(name)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Export not ready")
    return FileResponse(path, filename=os.path.basename(path))


# --------------------------------------------------------------------------- #
# Optional module install / uninstall (runs pip in the background)
# --------------------------------------------------------------------------- #
MODULE_JOBS = {}  # name -> {"status": running|done|error, "output": str}


def _run_module_job(name, action):
    from core.module_manager import install_module, uninstall_module, upgrade_module
    fn = {"install": install_module, "uninstall": uninstall_module,
          "upgrade": upgrade_module}[action]
    ok, out = fn(name)
    with _TASKS_LOCK:
        MODULE_JOBS[name] = {"status": "done" if ok else "error", "output": out}


@app.post("/api/modules/{action}")
async def module_action(action: str, payload: dict):
    _block_in_server_mode()
    if action not in ("install", "uninstall", "upgrade"):
        raise HTTPException(400, "action must be install|uninstall|upgrade")
    name = payload.get("name")
    from core.module_manager import MODULE_SPECS
    if name not in MODULE_SPECS:
        raise HTTPException(404, f"Unknown module: {name}")
    with _TASKS_LOCK:
        MODULE_JOBS[name] = {"status": "running", "output": ""}
    threading.Thread(target=_run_module_job, args=(name, action), daemon=True).start()
    return {"started": True}


@app.get("/api/modules/status")
def module_status_endpoint(name: str):
    with _TASKS_LOCK:
        job = dict(MODULE_JOBS.get(name, {"status": "idle", "output": ""}))
    # current availability (changes after a restart, but report live anyway)
    avail = {m["name"]: m["available"] for m in module_status()}
    job["available"] = avail.get(name, False)
    return job


@app.get("/api/modules/update-check")
def module_update_check(name: str):
    """Report whether a newer version of the module's package exists on PyPI.

    Reports only — the upgrade itself is the user-confirmed
    POST /api/modules/upgrade. Returns {} when there's nothing to report.
    """
    _block_in_server_mode()
    from core.module_manager import check_module_update
    return check_module_update(name) or {}


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
    host = ws.headers.get("host", "")
    allowed = {f"http://{host}", f"https://{host}",
               "http://localhost", "http://127.0.0.1"}
    if origin is not None and origin not in allowed:
        await ws.close(code=1008)
        return
    await ws.accept()
    target = ws.query_params.get("target", "zh")
    key = load_api_key_for_model("(Google) Live Translate")  # provider "Google"
    if not key:
        await ws.send_json({"type": "error", "message": "Google API key not set (Settings)."})
        await ws.close()
        return
    try:
        import websockets
    except Exception:
        await ws.send_json({"type": "error", "message": "websockets package missing."})
        await ws.close()
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

            await asyncio.gather(client_to_gemini(), gemini_to_client())
    except Exception as e:  # noqa: BLE001
        try:
            await ws.send_json({"type": "error", "message": str(e)[:300]})
        except Exception:
            pass
    finally:
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


@app.post("/api/live-preload")
async def live_preload():
    """Load the local STT model up front so the first utterance isn't blocked on
    a multi-second model load. The Live page calls this on Start."""
    if not video_translation_available():
        raise HTTPException(400, "Local STT needs the Video/Audio plugin (funasr).")
    from core.pipelines.video_translation_pipeline import (
        preload_recognizer, recognizer_ready)
    if recognizer_ready():
        return {"ready": True}
    loop = asyncio.get_event_loop()
    ready = await loop.run_in_executor(None, preload_recognizer)
    return {"ready": bool(ready)}


@app.post("/api/live-recognize")
async def live_recognize(payload: dict):
    """Step 1 of local live voice: recognize one utterance -> source text.
    Split from translation so the UI can show the source line immediately."""
    if not video_translation_available():
        raise HTTPException(400, "Local STT needs the Video/Audio plugin (funasr).")
    import base64
    try:
        pcm = base64.b64decode(payload.get("audio_b64", ""))
    except Exception:
        raise HTTPException(400, "Bad audio payload")
    if not pcm:
        return {"source": "", "detected": ""}
    from core.pipelines.video_translation_pipeline import recognize_utterance
    loop = asyncio.get_event_loop()

    def _recognize():
        with _STT_LOCK:
            return recognize_utterance(pcm, sample_rate=16000)
    source, detected = await loop.run_in_executor(None, _recognize)
    return {"source": source or "", "detected": detected or ""}


@app.post("/api/live-translate-text")
async def live_translate_text(payload: dict):
    """Step 2 of local live voice: translate a recognized line. Model/online are
    taken from the ACTIVE interface (no Settings checkbox)."""
    source = (payload.get("source") or "").strip()
    if not source:
        return {"translated": ""}
    dst_lang = payload.get("dst_lang", "en")
    src_code = payload.get("src_lang") or "auto"
    cfg = backend.read_config()
    use_online = bool(cfg.get("default_online", True))
    model = backend.get_active_model(use_online=use_online)
    api_key = (load_api_key_for_model(model)
               or os.environ.get("LINGUAHARU_API_KEY", "")) if use_online else ""
    from core.llm.llm_wrapper import translate_text_simple
    loop = asyncio.get_event_loop()

    def _translate():
        return translate_text_simple(source, src_code, dst_lang, model, use_online, api_key)
    try:
        translated, ok, _usage = await loop.run_in_executor(None, _translate)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Translate failed: {e}")
    return {"translated": translated if ok else ""}


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


class _NoCacheStatic(StaticFiles):
    """StaticFiles that asks browsers to revalidate every time, so UI edits to
    style.css / app.js show up on reload instead of serving a stale cached copy
    (browsers cache CSS heuristically when no Cache-Control is sent)."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


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


if __name__ == "__main__":
    import uvicorn
    host = server_host()
    port = server_port(host)
    print(f"LinguaHaru Web → http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    uvicorn.run(app, host=host, port=port)
