"""LinguaHaru Web — FastAPI backend (replaces the Gradio app).

The translation backend is fully reused from qt_app.backend (which is Qt-free,
pure-Python glue) and config.api_keys, so this layer is a thin HTTP wrapper:
  - serves the custom frontend (webapp/static)
  - exposes config / models / glossary / API-key endpoints
  - runs a translation in a background thread and streams progress over SSE

Run:  uvicorn webapp.server:app  (or python -m webapp.server)
"""
import os
import json
import shutil
import threading
import uuid
import queue

from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.responses import (
    FileResponse, StreamingResponse, JSONResponse, HTMLResponse)
from fastapi.staticfiles import StaticFiles

from qt_app import backend
from config.api_keys import (
    load_api_key_for_model, save_api_key_for_model, provider_of)
from config.languages_config import LABEL_TRANSLATIONS, LANGUAGE_MAP
from config.optional_modules import module_status, MEDIA_EXTENSIONS
from pipeline.video_translation_pipeline import (
    STT_MODELS, get_selected_stt_model, get_stt_model, SENSEVOICE_SUPPORTED_CODES)
from config.log_config import app_logger

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
# Uploads must live OUTSIDE the translation temp dir: DocumentTranslator.process()
# wipes temp/ on a fresh run, which would delete the file being translated.
UPLOAD_DIR = os.path.join(backend.REPO_ROOT, "web_uploads")

app = FastAPI(title="LinguaHaru Web")

# task_id -> {progress, desc, status, output, error, stop}
TASKS = {}
_TASKS_LOCK = threading.Lock()


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
    return {
        "languages": backend.available_languages(),
        "online_models": backend.scan_online_models(),
        "local_models": backend.scan_local_models(),
        "glossaries": backend.get_glossary_files(),
        "stt_models": [{"id": m["id"], "label": m["label"]} for m in STT_MODELS],
        "sensevoice_codes": sorted(SENSEVOICE_SUPPORTED_CODES),
        "language_map": LANGUAGE_MAP,
        "modules": module_status(),
        "config": {
            "default_online": online,
            "default_online_model": config.get("default_online_model", ""),
            "default_src_lang": config.get("default_src_lang", "English"),
            "default_dst_lang": config.get("default_dst_lang", "中文"),
            "default_glossary": config.get("default_glossary", "Default"),
            "stt_model": get_selected_stt_model(),
            "translate_subtitles": config.get("translate_subtitles", True),
            "max_retries": config.get("max_retries", 4),
            "thread_count": config.get("default_thread_count_online", 8) if online
            else config.get("default_thread_count_offline", 4),
        },
        "labels": LABEL_TRANSLATIONS,
    }


@app.post("/api/config")
async def update_config(payload: dict):
    """Persist arbitrary settings keys (whitelisted)."""
    allowed = {"default_online", "default_online_model", "default_src_lang",
               "default_dst_lang", "default_glossary", "stt_model",
               "translate_subtitles", "max_retries", "rpm_limit",
               "auto_extract_glossary"}
    config = backend.read_config()
    for k, v in payload.items():
        if k in allowed:
            config[k] = v
    backend.write_config(config)
    return {"ok": True}


@app.get("/api/apikey")
def get_apikey(model: str):
    """Whether a key exists for this model's provider (never returns the key)."""
    key = load_api_key_for_model(model)
    return {"provider": provider_of(model), "has_key": bool(key)}


@app.post("/api/apikey")
async def set_apikey(payload: dict):
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
def _run_translation(task_id, file_path, model, use_online, src_lang, dst_lang,
                     glossary_name, bilingual_flags):
    """Background worker: mirrors qt_app.worker._translate, pushing progress
    into TASKS[task_id]."""
    def set_state(**kw):
        with _TASKS_LOCK:
            TASKS[task_id].update(kw)

    try:
        ext = os.path.splitext(file_path)[1]
        stem = os.path.splitext(file_path)[0]
        translator_class = backend.get_translator_class(ext, **bilingual_flags)
        if translator_class is None:
            set_state(status="error", error=f"Unsupported file type '{ext}'.")
            return

        api_key = load_api_key_for_model(model) if use_online else ""
        src_code = backend.language_code(src_lang)
        dst_code = backend.language_code(dst_lang)
        gpath = backend.glossary_path(glossary_name) if glossary_name else None
        temp_dir, result_dir, log_dir = backend.get_custom_paths()
        config = backend.read_config()

        from config.log_config import file_logger
        file_logger.create_file_log(os.path.basename(file_path), log_dir=log_dir)

        translator = translator_class(
            file_path, model, use_online, api_key, src_code, dst_code, False,
            max_token=config.get("max_token", 768),
            max_retries=config.get("max_retries", 4),
            thread_count=config.get("default_thread_count_online", 8) if use_online
            else config.get("default_thread_count_offline", 4),
            glossary_path=gpath, temp_dir=temp_dir, result_dir=result_dir,
            session_lang="en", log_dir=log_dir,
        )

        def check_stop():
            with _TASKS_LOCK:
                if TASKS[task_id].get("stop"):
                    raise RuntimeError("__stopped__")
        translator.check_stop_requested = check_stop

        def progress_callback(value, desc=None):
            check_stop()
            set_state(progress=float(value), desc=desc or "")

        progress_callback(0.0, "Extracting text...")
        output_path, _missing = translator.process(stem, ext, progress_callback=progress_callback)
        set_state(status="done", progress=1.0, desc="Translation completed",
                  output=output_path)
    except RuntimeError as e:
        if "__stopped__" in str(e):
            set_state(status="stopped", desc="Stopped")
        else:
            app_logger.exception("Web translation error")
            set_state(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        app_logger.exception("Web translation error")
        set_state(status="error", error=str(e))


@app.post("/api/translate")
async def translate(
    file: UploadFile,
    src_lang: str = Form(...),
    dst_lang: str = Form(...),
    model: str = Form(...),
    use_online: bool = Form(True),
    glossary: str = Form(""),
    bilingual: bool = Form(False),
):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = os.path.basename(file.filename or "upload")
    dest = os.path.join(UPLOAD_DIR, safe_name)
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    task_id = uuid.uuid4().hex[:12]
    with _TASKS_LOCK:
        TASKS[task_id] = {"progress": 0.0, "desc": "Queued...",
                          "status": "running", "output": None, "error": None,
                          "stop": False}
    # The same bilingual flag drives every per-format mode (matches the UI).
    flags = {k: bilingual for k in (
        "excel_bilingual_mode", "word_bilingual_mode", "pdf_bilingual_mode",
        "subtitle_bilingual_mode", "txt_bilingual_mode", "md_bilingual_mode",
        "epub_bilingual_mode", "html_bilingual_mode")}
    threading.Thread(target=_run_translation, args=(
        task_id, dest, model, use_online, src_lang, dst_lang, glossary, flags),
        daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/progress/{task_id}")
def progress(task_id: str):
    if task_id not in TASKS:
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
def stop(task_id: str):
    with _TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]["stop"] = True
    return {"ok": True}


@app.get("/api/download/{task_id}")
def download(task_id: str):
    with _TASKS_LOCK:
        state = dict(TASKS.get(task_id, {}))
    out = state.get("output")
    if not out or not os.path.exists(out):
        raise HTTPException(404, "Result not ready")
    return FileResponse(out, filename=os.path.basename(out))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
