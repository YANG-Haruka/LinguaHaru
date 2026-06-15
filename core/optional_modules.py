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
    return (importlib.util.find_spec("faster_whisper") is not None
            or importlib.util.find_spec("funasr") is not None)


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
    {"id": "small",  "label": "PP-OCRv6 Small（轻量·快·推荐）", "info": "det+rec ≈ 100MB"},
    {"id": "medium", "label": "PP-OCRv6 Medium（高精度·较慢）", "info": "det+rec ≈ 140MB"},
    {"id": "tiny",   "label": "PP-OCRv6 Tiny（最快·精度略低）", "info": "det+rec ≈ 55MB"},
]


def ocr_models():
    """The selectable image-OCR (PP-OCRv6) models, for the Model Management UI."""
    return list(_OCR_MODELS)


def get_selected_ocr_model():
    from core import backend
    return backend.get_config("ocr_model_size", "small")


def _stt_catalog():
    from core.pipelines.video_translation_pipeline import STT_MODELS
    return [{"id": m["id"], "label": m["label"],
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
            ip._ocr_engine = None          # force re-create with the new size
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


def module_status():
    """Status of each optional plugin for the UI. Every plugin is uniform:
    install/uninstall + (where applicable) a model selector + current model."""
    ocr_engine = ("PP-OCRv6 (PaddleOCR)"
                  if importlib.util.find_spec("paddleocr") is not None
                  else "PP-OCRv5 (RapidOCR)")
    specs = _plugin_model_specs()

    def _entry(name, key, available, detail, requirements):
        spec = specs.get(name)
        return {
            "name": name, "key": key, "available": available, "detail": detail,
            "install": f"pip install -r {requirements}",
            "models": spec["models"] if spec else None,
            "current_model": plugin_current_model(name) if spec else None,
        }

    return [
        _entry("PDF", "pdf", pdf_translation_available(),
               "BabelDOC", "requirements/pdf.txt"),
        _entry("Image OCR", "ocr", image_translation_available(),
               ocr_engine, "requirements/ocr.txt"),
        _entry("Video/Audio", "video", video_translation_available(),
               "faster-whisper / SenseVoice · ffmpeg 已内置 · 视频字幕", "requirements/video.txt"),
        _entry("Real-Time Voice", "live", realtime_voice_available(),
               "SenseVoice / faster-whisper · 麦克风即时口译", "requirements/video.txt"),
        _entry("翻译语音输入", "speechio", quick_voice_available(),
               "edge-tts 朗读 + 语音输入 · 速译", "requirements/speechio.txt"),
    ]
