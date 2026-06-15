"""Short-text "quick translate" (Google-Translate-style): one-shot LLM text
translation + a small recent-history list. Text translation needs no extra
deps (same backend as document translation); voice input reuses the STT plugin.

Shared by Web (webapp/server.py endpoints) and Qt (qt_app/quick_page.py) so both
ends behave identically. History persists under the writable data dir."""
import json
import os
import threading

from core.paths import DATA_DIR
from core.log_config import app_logger

MAX_HISTORY = 50
_lock = threading.Lock()


def _history_path(store_dir=None):
    """Per-user history file. The Web app passes a per-session dir so users on a
    shared/LAN deploy never see each other's history; Qt (single local user)
    uses the default data dir."""
    return os.path.join(store_dir or DATA_DIR, "quick_history.json")


def translate(text, src_lang, dst_lang):
    """Translate short text via the ACTIVE interface (same model resolution as
    live voice / document translation). Returns (translated, ok)."""
    text = (text or "").strip()
    if not text:
        return "", False
    from core import backend
    from core.api_keys import load_api_key_for_model
    from core.llm.llm_wrapper import translate_text_simple
    use_online = bool(backend.get_config("default_online", True))
    model = backend.get_active_model(use_online)
    api_key = load_api_key_for_model(model) if use_online else ""
    translated, ok, _usage = translate_text_simple(
        text, src_lang or "auto", dst_lang, model, use_online, api_key)
    return (translated if ok else ""), bool(ok)


def get_history(store_dir=None):
    """Most-recent-first list of {src, translated, src_lang, dst_lang} (<=50)."""
    path = _history_path(store_dir)
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
            return items[:MAX_HISTORY] if isinstance(items, list) else []
        except Exception:  # noqa: BLE001 — missing/corrupt file -> empty history
            return []


def add_history(src, translated, src_lang, dst_lang, store_dir=None):
    """Prepend an entry (deduping an identical prior one), cap at MAX_HISTORY,
    persist to the per-user file. Returns the updated list."""
    src = (src or "").strip()
    if not src or not translated:
        return get_history(store_dir)
    path = _history_path(store_dir)
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                items = []
        except Exception:  # noqa: BLE001
            items = []
        items = [it for it in items
                 if not (it.get("src") == src and it.get("src_lang") == src_lang
                         and it.get("dst_lang") == dst_lang)]
        items.insert(0, {"src": src, "translated": translated,
                         "src_lang": src_lang, "dst_lang": dst_lang})
        items = items[:MAX_HISTORY]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Could not save quick-translate history: {e}")
        return items


def clear_history(store_dir=None):
    path = _history_path(store_dir)
    with _lock:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:  # noqa: BLE001
            pass
    return []
