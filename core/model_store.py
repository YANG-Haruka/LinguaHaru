"""Central, single-location store for all downloaded ML models.

Every engine (SenseVoice/funasr, faster-whisper, BabelDOC layout model, OCR)
downloads into ONE folder — `data/models` by default, or a user-chosen path in
system_config ("models_dir"). `setup_model_env()` points the libraries' cache
env vars there and must run BEFORE those libraries are imported. The Settings
"Model Management" view uses `list_models()` / `current_dir()`, and a location
change can `migrate_to()` the existing files.
"""
import os
import shutil

from core.paths import DATA_DIR, SYSTEM_CONFIG
from core.log_config import app_logger

_DEFAULT = os.path.join(DATA_DIR, "models")


def _read_cfg():
    try:
        import json
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def current_dir():
    """The configured models directory (absolute), defaulting to data/models."""
    cfg = _read_cfg()
    d = cfg.get("models_dir") or _DEFAULT
    return os.path.abspath(d)


def setup_model_env():
    """Point every model library's cache at the unified dir. Idempotent; uses
    setdefault so an explicit user env var still wins. Call at app startup."""
    md = current_dir()
    os.makedirs(md, exist_ok=True)
    # faster-whisper + BabelDOC layout model + any huggingface_hub download
    os.environ.setdefault("HF_HOME", md)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(md, "hub"))
    # funasr/modelscope fallback
    os.environ.setdefault("MODELSCOPE_CACHE", os.path.join(md, "modelscope"))
    # China-friendly mirror for huggingface (kept consistent with SenseVoice).
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    return md


def whisper_dir():
    """Download root for faster-whisper models (under the unified dir)."""
    d = os.path.join(current_dir(), "whisper")
    os.makedirs(d, exist_ok=True)
    return d


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human_size(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0


# Known model folders -> friendly label (matched by substring on the dir name).
_KNOWN = [
    ("SenseVoiceSmall", "SenseVoice (语音转文字)"),
    ("whisper", "Faster-Whisper (语音转文字)"),
    ("faster-whisper", "Faster-Whisper (语音转文字)"),
    ("fsmn-vad", "FSMN-VAD (语音断句)"),
    ("DocLayout", "BabelDOC 版面模型 (PDF)"),
    ("doclayout", "BabelDOC 版面模型 (PDF)"),
    ("PP-OCR", "PaddleOCR (图片识别)"),
    ("rapidocr", "RapidOCR (图片识别)"),
]


def list_models():
    """Downloaded models as dicts: {name, label, path, size, size_h}. Scans the
    unified dir one or two levels deep for recognizable model folders."""
    base = current_dir()
    found = []
    seen = set()
    if not os.path.isdir(base):
        return found

    def _label(name):
        for key, lbl in _KNOWN:
            if key.lower() in name.lower():
                return lbl
        return name

    # Walk a bounded depth so huge trees don't stall the UI.
    for root, dirs, _files in os.walk(base):
        # Skip HF bookkeeping dirs (.locks/.cache) entirely.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        depth = root[len(base):].count(os.sep)
        if depth >= 3:
            dirs[:] = []
            continue
        for d in list(dirs):
            full = os.path.join(root, d)
            if full in seen:
                continue
            if any(k.lower() in d.lower() for k, _ in _KNOWN):
                size = _dir_size(full)
                if size <= 0:
                    continue  # empty placeholder dir, not actually downloaded
                found.append({"name": d, "label": _label(d), "path": full,
                              "size": size, "size_h": human_size(size)})
                seen.add(full)
                dirs.remove(d)  # don't descend into a counted model
    found.sort(key=lambda m: m["label"])
    return found


def set_models_dir(new_dir, move=False):
    """Persist a new models directory. If move=True, relocate existing model
    files there first. Returns (ok, message)."""
    new_dir = os.path.abspath(new_dir)
    old_dir = current_dir()
    if new_dir == old_dir:
        return True, "unchanged"
    try:
        os.makedirs(new_dir, exist_ok=True)
        if move and os.path.isdir(old_dir):
            for entry in os.listdir(old_dir):
                src = os.path.join(old_dir, entry)
                dst = os.path.join(new_dir, entry)
                if os.path.exists(dst):
                    continue  # don't clobber
                shutil.move(src, dst)
        import json
        cfg = _read_cfg()
        cfg["models_dir"] = new_dir
        with open(SYSTEM_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        app_logger.info(f"Models directory set to {new_dir} (moved={move})")
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"Failed to set models dir: {e}")
        return False, str(e)
