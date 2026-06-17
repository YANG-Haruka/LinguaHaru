# Availability detection for optional translation modules.
# Core formats are always available; PDF, image and video translation light
# up when their extra dependencies are installed (requirements/pdf.txt,
# requirements/ocr.txt, requirements/video.txt).
import importlib.util
import os
import shutil

IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".bmp", ".webp"]
MEDIA_EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"]


def ffmpeg_exe():
    """Path to an ffmpeg executable.

    Prefers the pip-bundled binary from `imageio-ffmpeg` (installed with the
    Video/Audio plugin, so NO system/PATH install is needed), then falls back to
    an ffmpeg already on PATH. Returns None if neither is available."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:  # noqa: BLE001 — package missing or download failed
        pass
    return shutil.which("ffmpeg")


def pdf_translation_available():
    return importlib.util.find_spec("babeldoc") is not None


def image_translation_available():
    # Either the lightweight engine (rapidocr) or PaddleOCR works
    has_engine = (importlib.util.find_spec("rapidocr") is not None
                  or importlib.util.find_spec("paddleocr") is not None)
    return has_engine and all(importlib.util.find_spec(mod) is not None
                              for mod in ("cv2", "PIL"))


def _has_stt():
    return any(importlib.util.find_spec(m) is not None
               for m in ("faster_whisper", "funasr", "qwen_asr"))


def video_translation_available():
    # Video/audio file transcription needs an STT engine AND ffmpeg (to extract
    # the audio track); ffmpeg may be the pip-bundled imageio-ffmpeg binary.
    return _has_stt() and ffmpeg_exe() is not None


def realtime_voice_available():
    # Real-time voice only needs an STT engine — mic audio is captured
    # client-side as PCM, so NO ffmpeg is required (unlike file transcription).
    return _has_stt()


def tts_available():
    return importlib.util.find_spec("edge_tts") is not None


def quick_voice_available():
    # The "翻译语音输入" plugin powers the Quick-Translate audio buttons:
    # read-aloud (edge-tts TTS) AND voice input (STT). Both required so the two
    # buttons enable together (per the single-plugin design).
    return tts_available() and _has_stt()


def available_optional_extensions():
    extensions = []
    if pdf_translation_available():
        extensions.append(".pdf")
    if image_translation_available():
        extensions.extend(IMAGE_EXTENSIONS)
    if video_translation_available():
        extensions.extend(MEDIA_EXTENSIONS)
    return extensions


# ---------------------------------------------------------------------------
# Per-plugin model selection. Each plugin (where it has models) exposes a list,
# a current selection (config), and uniform install/switch behaviour:
#   install a plugin  -> auto-download its DEFAULT model
#   switch the model  -> download the NEW model
# ---------------------------------------------------------------------------

def _cfg_read():
    try:
        import json
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _cfg_write(key, value):
    import json
    from core.paths import SYSTEM_CONFIG
    cfg = _cfg_read()
    cfg[key] = value
    with open(SYSTEM_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


_OCR_MODELS = [
    {"id": "small",  "label": "PP-OCRv6 Small", "tags": ["Tag Lightweight", "Tag Recommended"],
     "size": "≈ 100 MB", "info": "det+rec ≈ 100MB"},
    {"id": "medium", "label": "PP-OCRv6 Medium", "tags": ["Tag HighAccuracy"],
     "size": "≈ 140 MB", "info": "det+rec ≈ 140MB"},
    {"id": "tiny",   "label": "PP-OCRv6 Tiny", "tags": ["Tag Fastest"],
     "size": "≈ 55 MB", "info": "det+rec ≈ 55MB"},
]

# Per-model tags (i18n keys, resolved in the UI) for the speech-to-text catalog.
_STT_TAGS = {
    "sensevoice-small":       ["Tag Recommended", "Tag CJKStrong"],
    "whisper-tiny":           ["Tag Fastest"],
    "whisper-base":           [],
    "whisper-small":          ["Tag Balanced"],
    "whisper-large-v3-turbo": ["Tag HighAccuracy"],
    "qwen3-asr-0.6b":         ["Tag Experimental"],
    "qwen3-asr-1.7b":         ["Tag Experimental"],
}

# model id -> folder-name substrings identifying its files on disk (for the
# downloaded-or-not check and for deletion). Kept here so the model catalog and
# its storage footprint stay in one place.
_MODEL_PROBES = {
    "small":  ["PP-OCRv6_small"],
    "medium": ["PP-OCRv6_medium"],
    "tiny":   ["PP-OCRv6_tiny"],
    "sensevoice-small":       ["SenseVoiceSmall"],
    "whisper-tiny":           ["faster-whisper-tiny"],
    "whisper-base":           ["faster-whisper-base"],
    "whisper-small":          ["faster-whisper-small"],
    "whisper-large-v3-turbo": ["faster-whisper-large-v3-turbo"],
    "qwen3-asr-0.6b":         ["Qwen3-ASR-0.6B"],
    "qwen3-asr-1.7b":         ["Qwen3-ASR-1.7B"],
}


def ocr_models():
    """The selectable image-OCR (PP-OCRv6) models, for the Model Management UI."""
    return list(_OCR_MODELS)


def get_selected_ocr_model():
    from core import backend
    return backend.get_config("ocr_model_size", "small")


def _stt_catalog():
    from core.pipelines.video_translation_pipeline import STT_MODELS
    return [{"id": m["id"], "label": m["label"], "tags": _STT_TAGS.get(m["id"], []),
             "size": m.get("disk", ""), "vram": m.get("vram", ""),
             "info": f"{m.get('disk', '')} · 显存 {m.get('vram', '')}".strip(" ·")}
            for m in STT_MODELS]


def _stt_default():
    from core.pipelines.video_translation_pipeline import DEFAULT_STT_MODEL
    return DEFAULT_STT_MODEL


# name -> {config_key, default, models()}  (only plugins that have a model choice)
def _plugin_model_specs():
    return {
        "Image OCR":       {"config_key": "ocr_model_size", "default": "small",
                            "models": list(_OCR_MODELS)},
        "Video/Audio":     {"config_key": "stt_model",      "default": _stt_default(),
                            "models": _stt_catalog()},
        "Real-Time Voice": {"config_key": "live_stt_model", "default": _stt_default(),
                            "models": _stt_catalog()},
        "翻译语音输入":      {"config_key": "quick_stt_model", "default": _stt_default(),
                            "models": _stt_catalog()},
    }


def plugin_current_model(name):
    spec = _plugin_model_specs().get(name)
    if not spec:
        return None
    val = _cfg_read().get(spec["config_key"])
    ids = {m["id"] for m in spec["models"]}
    return val if val in ids else spec["default"]


def set_plugin_model(name, model_id):
    """Persist the chosen model id for a plugin (does NOT download)."""
    spec = _plugin_model_specs().get(name)
    if not spec or model_id not in {m["id"] for m in spec["models"]}:
        return False
    _cfg_write(spec["config_key"], model_id)
    # Switching an STT model: free the previously-loaded one if nothing else uses it.
    if spec["config_key"] in ("stt_model", "live_stt_model", "quick_stt_model"):
        try:
            from core.pipelines.video_translation_pipeline import release_unused_stt_models
            release_unused_stt_models()
        except Exception:  # noqa: BLE001
            pass
    return True


def download_plugin_model(name, model_id=None):
    """Download (and warm) a plugin's model. If model_id is given it is persisted
    first. Heavy + blocking — callers run it in a background thread. Returns bool."""
    spec = _plugin_model_specs().get(name)
    if model_id and spec:
        set_plugin_model(name, model_id)
    try:
        if name == "Image OCR":
            import core.pipelines.image_translation_pipeline as ip
            ip._ocr_engines.clear()        # drop cached engines -> re-create with new size
            import gc; gc.collect()
            ip._get_ocr_engine()           # constructs PaddleOCR -> downloads models
            return True
        if name in ("Video/Audio", "Real-Time Voice", "翻译语音输入"):
            # Download the default STT model so voice input is ready (TTS/edge-tts
            # is online, needs no model).
            from core.pipelines.video_translation_pipeline import preload_recognizer
            return bool(preload_recognizer(plugin_current_model(name)))
        if name == "PDF":
            from core import model_store
            model_store.redirect_babeldoc_cache()
            from babeldoc.assets.assets import get_doclayout_onnx_model_path
            get_doclayout_onnx_model_path()
            return True
    except Exception as e:  # noqa: BLE001
        from core.log_config import app_logger
        app_logger.warning(f"download_plugin_model({name}) failed: {e}")
    return False


def plugin_model_states(name):
    """Per-model state for a plugin's catalog: each model's id/label/tags/size,
    whether it's downloaded on disk, and whether it's the active one. Powers the
    'expand a model type -> install / delete / use' UI on both frontends."""
    spec = _plugin_model_specs().get(name)
    if not spec:
        return []
    active = plugin_current_model(name)
    subs = [s for m in spec["models"] for s in _MODEL_PROBES.get(m["id"], [])]
    try:
        from core import model_store
        hits = model_store.find_model_dirs(subs) if subs else {}
    except Exception:  # noqa: BLE001
        hits = {}
    out = []
    for m in spec["models"]:
        downloaded = any(hits.get(s.lower()) for s in _MODEL_PROBES.get(m["id"], []))
        out.append({
            "id": m["id"], "label": m["label"], "tags": m.get("tags", []),
            "size": m.get("size", m.get("info", "")), "vram": m.get("vram", ""),
            "downloaded": bool(downloaded), "active": m["id"] == active,
        })
    return out


def delete_plugin_model(name, model_id):
    """Delete a specific model's files from disk. Frees it from memory first so
    the files aren't locked. Returns True if anything was removed."""
    subs = _MODEL_PROBES.get(model_id)
    if not subs:
        return False
    try:
        if name == "Image OCR":
            import core.pipelines.image_translation_pipeline as ip
            ip._ocr_engines.clear()        # release the cached engine (Windows file lock)
            import gc; gc.collect()
        else:
            from core.pipelines.video_translation_pipeline import release_stt_model
            release_stt_model(model_id)
    except Exception:  # noqa: BLE001
        pass
    from core import model_store
    removed = model_store.delete_model_dirs(subs) > 0
    # If we just deleted the plugin's ACTIVE model, switch its config to another
    # still-downloaded model so the backend doesn't try to use/redownload it.
    if removed:
        spec = _plugin_model_specs().get(name)
        if spec and plugin_current_model(name) == model_id:
            states = plugin_model_states(name)
            other = next((s["id"] for s in states if s["downloaded"] and s["id"] != model_id), None)
            if other:
                set_plugin_model(name, other)
    return removed


# Plugins whose model files are SHARED (the STT stack: SenseVoice/Whisper/Qwen
# used by Video/Audio + Real-Time Voice + 翻译语音输入). Uninstalling one of these
# must NOT delete the shared models while a sibling still uses them.
_SHARED_MODEL_PLUGINS = {"Video/Audio", "Real-Time Voice", "翻译语音输入"}


def _delete_pdf_model():
    """Delete BabelDOC's cached DocLayout model + assets (data/models/babeldoc)."""
    import shutil
    from core import model_store
    bd = os.path.join(model_store.current_dir(), "babeldoc")
    if os.path.isdir(bd):
        shutil.rmtree(bd, ignore_errors=True)
        return True
    return False


def cleanup_plugin_models(name):
    """Delete a plugin's model files on uninstall, but ONLY when they aren't
    shared: OCR/PDF models are removed; the STT models are kept (Video/Audio +
    Real-Time Voice + 翻译语音输入 share them). Returns the list of removed model
    ids/labels. Never raises."""
    removed = []
    try:
        if name == "Image OCR":
            for st in plugin_model_states(name):
                if st.get("downloaded") and delete_plugin_model(name, st["id"]):
                    removed.append(st["id"])
        elif name == "PDF":
            if _delete_pdf_model():
                removed.append("DocLayout")
        # STT plugins (_SHARED_MODEL_PLUGINS): models shared -> kept on purpose.
    except Exception as e:  # noqa: BLE001 — model cleanup must not fail the uninstall
        from core.log_config import app_logger
        app_logger.warning(f"cleanup_plugin_models({name}) failed: {e}")
    return removed


def uninstall_plugin(name):
    """Uninstall a plugin the way the user expects:
    - remove its pip deps that are NOT shared with any other plugin (a shared
      dependency, e.g. the STT stack, is kept while a sibling still needs it);
    - delete its model files ONLY when the model isn't shared (OCR/PDF models are
      removed; the STT models stay because Video/Audio + Real-Time Voice +
      翻译语音输入 share them — they can be deleted in Model Management once no
      voice plugin needs them).
    Returns (ok, message)."""
    from core import module_manager
    ok, out = module_manager.uninstall_module(name)   # shared-aware pip removal
    removed_models = cleanup_plugin_models(name)
    if removed_models:
        out = f"{out} | removed models: {', '.join(removed_models)}"
    return ok, out


def module_status():
    """Status of each optional plugin for the UI. Every plugin is uniform:
    install/uninstall + (where applicable) a model selector + current model."""
    ocr_engine = ("PP-OCRv6 (PaddleOCR)"
                  if importlib.util.find_spec("paddleocr") is not None
                  else "PP-OCRv5 (RapidOCR)")
    specs = _plugin_model_specs()

    def _entry(name, key, available, detail, requirements, fixed_model=None):
        spec = specs.get(name)
        return {
            "name": name, "key": key, "available": available, "detail": detail,
            "install": f"pip install -r {requirements}",
            "models": spec["models"] if spec else None,
            "current_model": plugin_current_model(name) if spec else None,
            # For plugins with a FIXED (non-selectable) model — shown read-only so
            # every plugin displays the model it uses.
            "fixed_model": fixed_model,
        }

    return [
        _entry("PDF", "pdf", pdf_translation_available(),
               "BabelDOC", "requirements/pdf.txt", fixed_model="DocLayout (BabelDOC)"),
        _entry("Image OCR", "ocr", image_translation_available(),
               ocr_engine, "requirements/ocr.txt"),
        _entry("Video/Audio", "video", video_translation_available(),
               "faster-whisper / SenseVoice · ffmpeg 已内置 · 视频字幕", "requirements/video.txt"),
        _entry("Real-Time Voice", "live", realtime_voice_available(),
               "SenseVoice / faster-whisper · 麦克风即时口译", "requirements/video.txt"),
        _entry("翻译语音输入", "speechio", quick_voice_available(),
               "edge-tts 朗读 + 语音输入 · 速译", "requirements/speechio.txt"),
    ]
