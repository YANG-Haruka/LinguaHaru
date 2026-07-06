"""Backend service layer shared by BOTH frontends (webapp/ and qt_app/). NO UI
imports here.

Everything a UI needs that touches the LinguaHaru translation backend or its
config lives here: extension -> translator-class resolution (with per-extension
bilingual partial() logic), system_config read/write helpers, model list
discovery, glossary CRUD, proofread helpers, and language helpers.
"""

import os
import json
import csv
from functools import partial
from importlib import import_module

from core.optional_modules import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS
from core.languages_config import (
    get_language_code, get_available_languages, LABEL_TRANSLATIONS,
)

from core.paths import RUNTIME_ROOT, DATA_DIR, SYSTEM_CONFIG, API_CONFIG_DIR

# Bundled (read-only) resources — config templates, requirements — are anchored
# here; from source it's the repo root, in a frozen build it's the bundle dir.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cap on concurrent file translations (both frontends honor it).
MAX_CONCURRENT_TASKS = 3

# --- Extension -> translator module (single source of truth) -----------------
TRANSLATOR_MODULES = {
    ".docx": "core.translators.word_translator.WordTranslator",
    ".pptx": "core.translators.ppt_translator.PptTranslator",
    ".xlsx": "core.translators.excel_translator.ExcelTranslator",
    ".pdf": "core.translators.pdf_translator.PdfTranslator",
    ".srt": "core.translators.subtitle_translator.SubtitlesTranslator",
    ".txt": "core.translators.txt_translator.TxtTranslator",
    ".md": "core.translators.md_translator.MdTranslator",
    ".epub": "core.translators.epub_translator.EpubTranslator",
    ".csv": "core.translators.csv_translator.CsvTranslator",
    ".tsv": "core.translators.csv_translator.CsvTranslator",
    ".html": "core.translators.extra_formats_translator.HtmlTranslator",
    ".htm": "core.translators.extra_formats_translator.HtmlTranslator",
    ".odt": "core.translators.extra_formats_translator.OdtTranslator",
    ".json": "core.translators.extra_formats_translator.JsonTranslator",
    ".vtt": "core.translators.extra_formats_translator.VttTranslator",
    ".ass": "core.translators.extra_formats_translator.AssTranslator",
    ".ssa": "core.translators.extra_formats_translator.AssTranslator",
    ".lrc": "core.translators.extra_formats_translator.LrcTranslator",
}
for _ext in IMAGE_EXTENSIONS:
    TRANSLATOR_MODULES[_ext] = "core.translators.image_translator.ImageTranslator"
for _ext in MEDIA_EXTENSIONS:
    TRANSLATOR_MODULES[_ext] = "core.translators.video_translator.VideoTranslator"


# File-format categories a LAN admin can allow/deny for normal users. Maps a
# stable feature key -> its extensions. Both frontends use it to render the
# format toggles; the server uses it to ENFORCE a denial (a normal LAN user must
# not be able to bypass the hidden UI and upload a denied type anyway).
FORMAT_CATEGORIES = {
    "fmt-word":     [".docx"],
    "fmt-ppt":      [".pptx"],
    "fmt-excel":    [".xlsx", ".csv", ".tsv"],
    "fmt-pdf":      [".pdf"],
    "fmt-subtitle": [".srt", ".vtt", ".ass", ".ssa", ".lrc"],
    "fmt-text":     [".txt", ".md", ".epub", ".html", ".htm", ".odt", ".json"],
    "fmt-image":    list(IMAGE_EXTENSIONS),
    "fmt-video":    list(MEDIA_EXTENSIONS),
}
_EXT_TO_FORMAT_KEY = {e: k for k, exts in FORMAT_CATEGORIES.items() for e in exts}


def format_key_for_ext(ext):
    """The format-category key for a file extension ('.mp4' -> 'fmt-video'), or
    None if the extension isn't a categorised type."""
    return _EXT_TO_FORMAT_KEY.get((ext or "").lower())


def accepted_extensions():
    """All extensions the UI should accept in the file picker (sorted)."""
    return sorted(TRANSLATOR_MODULES.keys())


def _pdf_is_scanned(path, sample=6):
    """True if the PDF looks like scanned images (little/no extractable text
    layer across the sampled pages). Such PDFs translate far better via manga/
    GPU-OCR mode than via BabelDOC, whose scanned-OCR path is weak and often
    yields 'no paragraphs'."""
    try:
        import fitz
        doc = fitz.open(path)
        try:
            n = min(sample, doc.page_count)
            if n == 0:
                return False
            for i in range(n):
                if len(doc[i].get_text("text").strip()) >= 20:
                    return False     # a real text layer exists -> not scanned
            return True
        finally:
            doc.close()
    except Exception as e:  # noqa: BLE001 — can't inspect -> assume not scanned
        from core.log_config import app_logger
        app_logger.debug(f"_pdf_is_scanned: could not inspect {path} ({e}); assuming digital")
        return False


def get_translator_class(
    file_extension, file_path=None,
    excel_mode_2=False, word_bilingual_mode=False, excel_bilingual_mode=False,
    pdf_bilingual_mode=False, subtitle_bilingual_mode=False, txt_bilingual_mode=False,
    md_bilingual_mode=False, epub_bilingual_mode=False, html_bilingual_mode=False,
):
    """Import and return the translator class (or a partial with the bilingual
    knobs bound) for a file extension. Copied from app.get_translator_class."""
    # 漫画模式 (manga_mode): a PDF is treated as a manga/scanned comic — rasterize
    # pages, bubble-group + translate as images, repack to PDF. Images already go
    # through the image pipeline (which honors manga_mode internally), so only the
    # PDF route needs swapping here. Besides the explicit toggle, a PDF with no
    # text layer (scanned images) is auto-routed here — BabelDOC can't translate
    # scans, but manga/GPU-OCR mode can (config auto_manga_scanned, default on).
    if file_extension.lower() == ".pdf":
        cfg = read_config()
        scanned = bool(file_path) and cfg.get("auto_manga_scanned", True) \
            and _pdf_is_scanned(file_path)
        if cfg.get("manga_mode", False) or scanned:
            from core.translators.manga_pdf_translator import MangaPdfTranslator
            if scanned and not cfg.get("manga_mode", False):
                from core.log_config import app_logger
                app_logger.info("Scanned PDF detected (no text layer) -> manga/GPU-OCR mode")
            return MangaPdfTranslator

    module_path = TRANSLATOR_MODULES.get(file_extension.lower())
    if not module_path:
        return None

    try:
        module_name, class_name = module_path.rsplit('.', 1)
        module = import_module(module_name)
        translator_class = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        # Don't fail silently: a missing bundled dep here surfaces to the user as
        # the misleading "Unsupported file type" — log the real cause.
        import traceback
        from core.log_config import app_logger
        app_logger.error(f"Translator load failed for '{file_extension}' "
                         f"({module_path}): {type(e).__name__}: {e}\n"
                         + traceback.format_exc())
        return None

    ext = file_extension.lower()
    if ext == ".xlsx":
        return partial(translator_class,
                       use_xlwings=excel_mode_2 or excel_bilingual_mode,
                       bilingual_mode=excel_bilingual_mode)
    if ext == ".docx":
        return partial(translator_class, bilingual_mode=word_bilingual_mode)
    if ext == ".pdf":
        return partial(translator_class, word_bilingual_mode=pdf_bilingual_mode)
    if ext in (".srt", ".vtt"):
        return partial(translator_class, bilingual_mode=subtitle_bilingual_mode)
    if ext == ".txt":
        return partial(translator_class, bilingual_mode=txt_bilingual_mode)
    if ext == ".md":
        return partial(translator_class, bilingual_mode=md_bilingual_mode)
    if ext == ".epub":
        return partial(translator_class, bilingual_mode=epub_bilingual_mode)
    if ext in (".html", ".htm"):
        return partial(translator_class, bilingual_mode=html_bilingual_mode)
    if ext in MEDIA_EXTENSIONS:
        # Video/audio output is a subtitle file -> reuse the subtitle toggle
        return partial(translator_class, bilingual_mode=subtitle_bilingual_mode)
    return translator_class


# Per-extension -> the config key controlling its bilingual SwitchButton. Used
# by the Translate page to show only the relevant toggles for uploaded files.
BILINGUAL_KEY_BY_EXT = {
    ".xlsx": "excel_bilingual_mode",
    ".docx": "word_bilingual_mode",
    ".pdf": "pdf_bilingual_mode",
    ".srt": "subtitle_bilingual_mode",
    ".vtt": "subtitle_bilingual_mode",
    ".txt": "txt_bilingual_mode",
    ".md": "md_bilingual_mode",
    ".epub": "epub_bilingual_mode",
    ".html": "html_bilingual_mode",
    ".htm": "html_bilingual_mode",
}
# Video/audio produce subtitle output, so they share the subtitle toggle.
for _media_ext in MEDIA_EXTENSIONS:
    BILINGUAL_KEY_BY_EXT[_media_ext] = "subtitle_bilingual_mode"

# Human label for each bilingual config key (English; for the SwitchButton text).
BILINGUAL_LABEL = {
    "excel_bilingual_mode": "Excel Bilingual",
    "word_bilingual_mode": "Word Bilingual",
    "pdf_bilingual_mode": "PDF Bilingual",
    "subtitle_bilingual_mode": "Subtitle Bilingual",
    "txt_bilingual_mode": "TXT Bilingual",
    "md_bilingual_mode": "MD Bilingual",
    "epub_bilingual_mode": "EPUB Bilingual",
    "html_bilingual_mode": "HTML Bilingual",
}


def bilingual_keys_for_files(file_paths):
    """Ordered, de-duplicated list of bilingual config keys relevant to the
    given uploaded files (so the UI shows just those toggles)."""
    keys = []
    for p in file_paths:
        ext = os.path.splitext(p)[1].lower()
        key = BILINGUAL_KEY_BY_EXT.get(ext)
        if key and key not in keys:
            keys.append(key)
    return keys


# --- system_config.json -----------------------------------------------------
# Writable + frozen-aware (persists next to the exe, seeded from the bundled
# default on first run) — NOT under the read-only bundle.
CONFIG_PATH = SYSTEM_CONFIG

_DEFAULT_CONFIG = {
    "lan_mode": False,
    "default_online": False,
    # Per-request input batch budget (prompt + source text). Bigger = fewer API
    # requests for the same document, which cuts overhead and rate-limit/429
    # pressure and gives the model more context. DeepSeek's 64K window leaves
    # plenty of room; we keep output bounded via each model's max_completion_tokens.
    "max_token": 4096,
    "max_retries": 4,
    "excel_mode_2": False,
    "excel_bilingual_mode": False,
    "word_bilingual_mode": False,
    "pdf_bilingual_mode": False,
    "subtitle_bilingual_mode": False,
    "txt_bilingual_mode": False,
    "md_bilingual_mode": False,
    "epub_bilingual_mode": False,
    "html_bilingual_mode": False,
    # 漫画模式: treat a PDF/image as a manga/scanned comic — bubble-group OCR lines,
    # translate each bubble as a sentence, render vertical CJK back, repack PDF->PDF.
    # Needs the Image OCR plugin. Off => normal document/image translation.
    "manga_mode": False,
    # Run OCR (PaddleOCR/RapidOCR) in a child process. It's CPU-bound and holds the
    # Python GIL, which would freeze the desktop UI thread if run in-process on a
    # QThread. Default on; set False to run in-process (debugging).
    "ocr_subprocess": True,
    "default_thread_count_online": 2,
    "default_thread_count_offline": 4,
    "default_src_lang": "English",
    "default_dst_lang": "English",
    "auto_extract_glossary": False,
    # Translation mode profile (sampling params + later prompt rules / QA).
    # See config/translation_modes.json. Default "standard" = DeepSeek's official
    # translation temperature (1.3); "precise" (0.1) is the low-temp alternative.
    "translation_mode": "standard",
    # Advanced translation modifiers (appended to the prompt; all optional).
    "translation_tone": "",     # "" | "formal" | "casual"
    "translation_length": "",   # "" | "keep" | "expand" | "short"
    "translation_style": "",    # free-text style guide
    # Pass each item's type to the LLM as a disambiguation context block (opt-in).
    "translate_with_context": False,
    "rpm_limit": 0,
    "qt_theme": "light",
    "qt_ui_lang": "zh",
    "default_online_model": "",
    "default_local_model": "",
    "temp_dir": "data/temp",
    "result_dir": "result",
    "log_dir": "log",
    # History retention: prune beyond N records and/or older than D days
    # (0 = unlimited / no age limit).
    "history_max_records": 1000,
    "history_max_age_days": 0,
    # Log + result retention (applied at startup; 0 = unlimited). Logs are pure
    # diagnostics so they're cleaned fairly aggressively; results are the user's
    # outputs so the size cap is generous and only trims the oldest.
    "log_max_files": 500,
    "log_max_age_days": 30,
    "log_max_size_mb": 500,
    "result_max_size_mb": 5000,
    # Max total bytes accepted per /api/translate request (videos/big PDFs need
    # headroom). Public deploys (server_mode) are capped lower in the server.
    "max_upload_mb": 2048,
    # When installing a torch-using plugin on an NVIDIA-GPU machine, install the
    # CUDA torch wheels from this index (so STT runs on the GPU, not the CPU).
    # Change to .../cu124 for newer drivers, or set "" to force the CPU build.
    "torch_cuda_index": "https://download.pytorch.org/whl/cu121",
    # Bilingual (双语对照): style the TRANSLATED text so it stands out from the
    # original. color is a hex without '#' ("" = no color). Subtitles (srt/vtt).
    "bilingual_bold": True,
    "bilingual_color": "",
    # Live captions: stream the translation token-by-token (online only). Opt-in.
    "live_stream_translation": False,
    # Web real-time VAD engine: "energy" (worklet) or "silero" (neural, noise-
    # robust, via onnxruntime-web). Qt uses TEN-VAD natively; this unifies the
    # Web side onto a neural VAD too. Opt-in until smoke-tested with a real mic.
    "web_vad": "energy",
}


def read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)


def write_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_config(key, default=None):
    return read_config().get(key, _DEFAULT_CONFIG.get(key, default))


def set_config(key, value):
    """Persist a single config key immediately."""
    config = read_config()
    config[key] = value
    write_config(config)
    return value


# --- LAN admin password hashing (shared by the Web server + the Qt LAN toggle) ---
_PBKDF2_ITERS = 200_000


def hash_lan_password(pw):
    """Salted PBKDF2-HMAC-SHA256 of a password. Only the hash is stored (config
    is git-tracked). Format: pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>."""
    import hashlib
    import secrets
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                             bytes.fromhex(salt), _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt}${dk.hex()}"


def verify_lan_password(pw, stored):
    """Constant-time check of a password against a stored PBKDF2 hash."""
    import hashlib
    import hmac
    try:
        algo, iters, salt, want = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), want)
    except Exception:  # noqa: BLE001
        return False


def get_custom_paths():
    """Resolve and create temp/result/log dirs (absolute, anchored at repo).

    log_dir is kept for backward-compat (resume metadata) but per-project logs now
    live in the project's RESULT folder, not here — see history_dir() for the
    translation-history database location."""
    config = read_config()
    paths = []
    for key, default in (("temp_dir", "data/temp"), ("result_dir", "result"), ("log_dir", "log")):
        d = config.get(key, default)
        if not os.path.isabs(d):
            d = os.path.join(RUNTIME_ROOT, d)   # writable runtime root, not bundle
        # Only create temp/result; log_dir is legacy (per-project logs live in the
        # result folder now) so we DON'T materialize an empty data/log.
        if key != "log_dir":
            os.makedirs(d, exist_ok=True)
        paths.append(d)
    return tuple(paths)


_history_migrated = False


def _migrate_history_once():
    """One-time move of the translation-history DB(s) from the old data/log
    location into data/history, so upgrading users keep their history when data/log
    goes away. Best-effort; never raises."""
    global _history_migrated
    if _history_migrated:
        return
    _history_migrated = True
    old_base = os.path.join(DATA_DIR, "log")
    new_base = os.path.join(DATA_DIR, "history")
    if not os.path.isdir(old_base):
        return
    import shutil
    try:
        os.makedirs(new_base, exist_ok=True)
        for name in os.listdir(old_base):
            src = os.path.join(old_base, name)
            if name == "translation_history.db":
                dst = os.path.join(new_base, name)
                if not os.path.exists(dst):
                    shutil.move(src, dst)
            elif os.path.isdir(src):   # per-session subdir holding its own DB
                db = os.path.join(src, "translation_history.db")
                if os.path.exists(db):
                    dst_dir = os.path.join(new_base, name)
                    os.makedirs(dst_dir, exist_ok=True)
                    dst = os.path.join(dst_dir, "translation_history.db")
                    if not os.path.exists(dst):
                        shutil.move(db, dst)
        # The old global system.log moved to data/system.log — drop the stale copy.
        for name in list(os.listdir(old_base)):
            if name.startswith("system.log"):
                try:
                    os.remove(os.path.join(old_base, name))
                except OSError:
                    pass
        # Remove now-empty leftovers so data/log disappears entirely (non-empty
        # dirs with legacy per-project .log files are left for the user to keep).
        for name in list(os.listdir(old_base)):
            p = os.path.join(old_base, name)
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
        if not os.listdir(old_base):
            os.rmdir(old_base)
    except Exception:  # noqa: BLE001 — migration is best-effort
        pass


def history_dir(session_id=None):
    """Where the translation-history DB lives: data/history (global, Qt) or
    data/history/<session_id> (web per-session isolation). NOT under data/log —
    history is data, not a log, so data/log can go away entirely."""
    _migrate_history_once()
    base = os.path.join(DATA_DIR, "history")
    if session_id:
        base = os.path.join(base, session_id)
    os.makedirs(base, exist_ok=True)
    return base


def thread_count_for_mode(use_online, model=None):
    """Worker-thread count for a translation run.

    Online models may declare a per-model "thread_count" in their api_config
    json (e.g. DeepSeek Flash → 16, Pro → 8), so a fast model can run more
    parallel requests than a slow one. Falls back to the global
    default_thread_count_online/offline when the model sets nothing.
    """
    config = read_config()
    # Per-model thread_count (set in the interface config) wins for both online
    # and offline models; local models get an api_config json once configured.
    if model:
        mc = read_api_config(model) or {}
        tc = mc.get("thread_count")
        if tc:
            try:
                return max(1, int(tc))
            except (TypeError, ValueError):
                pass
    if use_online:
        return config.get("default_thread_count_online", 8)
    return config.get("default_thread_count_offline", 4)


def max_retries_for_model(model=None):
    """Max translation retries. A per-model "max_retries" in the interface
    config overrides the global default (so a flaky model can retry more)."""
    if model:
        mc = read_api_config(model) or {}
        mr = mc.get("max_retries")
        if mr:
            try:
                return max(1, int(mr))
            except (TypeError, ValueError):
                pass
    return read_config().get("max_retries", 4)


def max_token_for_model(model=None):
    """Per-request input batch budget. A per-model "max_token" in the interface
    config wins (so a big-context model can batch more text per request); else the
    global config "max_token" (default 4096). Bigger batches => far fewer requests
    for a document => less rate-limit/429 pressure + better context.

    LOCAL models (Ollama / LM Studio) are the exception: they load a SMALL context
    window (often 4096) no matter the model's theoretical max, so the online 4096
    batch overflows it (input + reply > ctx => truncated/empty output, whole batch
    lost). For those we size the batch from the model's actual loaded context."""
    if model and (model.startswith("(LM Studio)") or model.startswith("(Ollama)")):
        return _local_batch_budget(model)
    if model:
        mc = read_api_config(model) or {}
        mt = mc.get("max_token")
        if mt:
            try:
                return max(128, int(mt))
            except (TypeError, ValueError):
                pass
        # A custom interface pointed at a LOCAL server (Ollama / LM Studio at
        # localhost, e.g. Hunyuan-MT) is a small-context local model too — the
        # online 4096 batch overflows its window (Ollama's default num_ctx is
        # 4096). Use a conservative batch unless the user set an explicit max_token.
        base_url = str(mc.get("base_url", "")).lower()
        if any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0")):
            return 1024
    return read_config().get("max_token", 4096)


def _local_batch_budget(model):
    """Input batch budget for a local (Ollama / LM Studio) model: ~35% of the
    model's loaded context window, leaving the rest for the prompt, accumulated
    context and the reply (incl. a reasoning model's hidden tokens). Honors a
    user-set per-model "max_token". Falls back to a conservative 1024 when the
    window can't be detected (e.g. the model isn't loaded yet) — small enough to
    be safe on a 4096-window model, which is the common default."""
    try:
        mc = read_api_config(model) or {}
        if mc.get("max_token"):
            return max(128, int(mc["max_token"]))
    except (TypeError, ValueError, OSError):
        pass
    ctx = None
    if model.startswith("(LM Studio)"):
        try:
            from core.llm.offline_translation import lm_studio_context
            ctx = lm_studio_context(model.split(")", 1)[1].strip())
        except Exception:  # noqa: BLE001 — probe is best-effort
            ctx = None
    if ctx and ctx > 0:
        # ~40% of the window for the input batch; translate_offline's dynamic reply
        # cap keeps input + reply within ctx. Capped at 4096 (the online batch size)
        # so a local model loaded with a BIG context uses FEW large batches instead
        # of many tiny ones — far fewer slow sequential local calls. A user who finds
        # a big document slow on a local model should load it with a larger context
        # window (e.g. 16384) in LM Studio; the batch then scales up automatically.
        return int(max(256, min(4096, ctx * 0.4)))
    return 1024


# --- Model list discovery (mirrors app.py startup logic) ---------------------
def scan_online_models():
    """Online models = api_config/*.json filenames (without .json). Reads the
    WRITABLE API_CONFIG_DIR so newly-added/fetched interfaces show up (in a
    frozen build the bundled dir is read-only and never gets user additions)."""
    try:
        return sorted(
            os.path.splitext(f)[0] for f in os.listdir(API_CONFIG_DIR)
            if f.endswith(".json") and f != "Custom.json"
        )
    except OSError:
        return []


def scan_local_models(force_refresh=False):
    """Local model list via the offline LLM wrapper (Ollama, etc.)."""
    from core.llm.offline_translation import populate_sum_model
    return populate_sum_model(force_refresh=force_refresh) or []


def fetch_online_models(selected_model, api_key):
    """Query the selected config's base_url for its model list, writing new
    '(Fetched) <id>.json' configs. Returns (models, status_message)."""
    from core.llm.online_translation import fetch_models_into_configs
    added, error = fetch_models_into_configs(selected_model, api_key)
    models = scan_online_models()
    if error:
        return models, f"Fetch failed: {error} (kept {len(models)} entries)"
    return models, f"Refreshed: {added} new model(s), {len(models)} total."


# --- Interface management (config/api_config/*.json) -------------------------
# API_CONFIG_DIR comes from core.paths: writable (seeded from the bundle) in a
# frozen build, the repo's config/api_config from source.

# Prefixes used by the bundled "official" provider configs. Anything not starting
# with one of these (and not the Custom template) is treated as user-added.
_OFFICIAL_PREFIXES = (
    "(Anthropic)", "(ChatGPT)", "(Deepseek)", "(Gemini)", "(GLM)", "(Grok)",
    "(Siliconflow)", "(Siliconflow Pro)", "(Volcengine)", "(Fetched)",
)


def list_online_interfaces():
    """All online interface configs as dicts: name, base_url, model, official."""
    interfaces = []
    try:
        files = sorted(f for f in os.listdir(API_CONFIG_DIR)
                       if f.endswith(".json") and f != "Custom.json")  # template, not an interface
    except OSError:
        return interfaces
    for f in files:
        name = os.path.splitext(f)[0]
        cfg = read_api_config(name) or {}
        official = name.startswith(_OFFICIAL_PREFIXES)
        interfaces.append({
            "name": name,
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "official": official,
        })
    return interfaces


def read_api_config(name):
    """Read a single api_config/<name>.json (or None)."""
    if not name:
        return None
    safe = os.path.basename(f"{name}.json")
    path = os.path.join(API_CONFIG_DIR, safe)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_api_config(name, data):
    """Merge the given keys into api_config/<name>.json (create if absent).

    MERGE (not overwrite) so saving from the config modal — which only sends a
    subset of fields — never wipes other per-model keys (rpm, thread_count,
    max_retries, presence/frequency_penalty). Empty/None values are ignored."""
    if not name:
        raise ValueError("Interface name is required.")
    os.makedirs(API_CONFIG_DIR, exist_ok=True)
    safe = os.path.basename(f"{name}.json")
    path = os.path.join(API_CONFIG_DIR, safe)
    existing = read_api_config(name) or {}
    cleaned = {k: v for k, v in data.items() if v not in ("", None)}
    existing.update(cleaned)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4, ensure_ascii=False)
    return path


def delete_api_config(name):
    """Delete an api_config/<name>.json. Returns True if removed."""
    if not name:
        return False
    safe = os.path.basename(f"{name}.json")
    path = os.path.join(API_CONFIG_DIR, safe)
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def get_active_model(use_online):
    """The persisted active model name for the given mode."""
    key = "default_online_model" if use_online else "default_local_model"
    return get_config(key, "")


def set_active_model(name, use_online):
    """Persist the active model for the given mode."""
    key = "default_online_model" if use_online else "default_local_model"
    return set_config(key, name)


# --- Optional module installation (pip in a subprocess) ----------------------
# Maps the optional-module name (as reported by core.optional_modules) to the
# requirements file that installs it (resolved from the plugin manifests in
# plugins/<key>/). The Plugins page runs these in a worker.
def install_command_for(module_name):
    """The pip command (list form) that installs the given optional module, or
    None if unknown."""
    from core import plugins_registry
    req_path = plugins_registry.requirements_path(module_name)
    if not req_path:
        return None
    import sys
    return [sys.executable, "-m", "pip", "install", "-r", req_path]


# --- Glossary (CSV, utf-8-sig, multi-encoding read) -------------------------
# User-editable content -> top-level glossary/ (next to result/), not buried in
# data/, so it's easy to find and edit.
GLOSSARY_DIR = os.path.join(RUNTIME_ROOT, "glossary")
_GLOSSARY_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "shift-jis")
# A glossary's header row is LANGUAGE CODES (text_separator.load_glossary matches
# the source/target columns by code/name) — NOT "source,target". A new glossary
# must therefore start with language columns or it would silently never apply.
_DEFAULT_GLOSSARY_HEADER = ["en", "zh", "zh-Hant", "ja", "es", "fr",
                            "de", "it", "pt", "ru", "ko", "th", "vi"]


def _ensure_default_glossary():
    """Guarantee at least a 'Default' glossary exists. A fresh install (incl. the
    portable, which used to ship no glossary/ at all) would otherwise have an empty
    picker and nothing to load/edit."""
    os.makedirs(GLOSSARY_DIR, exist_ok=True)
    try:
        if any(f.endswith(".csv") for f in os.listdir(GLOSSARY_DIR)):
            return
    except OSError:
        return
    _write_glossary_csv(os.path.join(GLOSSARY_DIR, "Default.csv"),
                        _DEFAULT_GLOSSARY_HEADER, [])


def get_glossary_files():
    """Glossary names (no .csv), Default first."""
    _ensure_default_glossary()
    try:
        files = [f for f in os.listdir(GLOSSARY_DIR) if f.endswith(".csv")]
    except OSError:
        return ["Default"]
    files.sort(key=lambda x: (x != "Default.csv", x.lower()))
    return [os.path.splitext(f)[0] for f in files]


def glossary_path(glossary_name):
    """Resolve a glossary name to a path strictly inside the glossary dir."""
    if not glossary_name:
        return None
    base = os.path.realpath(GLOSSARY_DIR)
    candidate = os.path.realpath(os.path.join(base, f"{glossary_name}.csv"))
    if not candidate.startswith(base + os.sep):
        return None
    if os.path.basename(candidate) != f"{glossary_name}.csv":
        return None
    return candidate


def load_glossary(glossary_name):
    """Return (header, rows) where rows is a list of list[str]. Tries several
    encodings (utf-8-sig first). Raises FileNotFoundError if missing."""
    path = glossary_path(glossary_name)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Glossary not found: {glossary_name}")
    last_error = None
    for enc in _GLOSSARY_ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = list(csv.reader(f))
            if not reader:
                return [], []
            return reader[0], reader[1:]
        except UnicodeDecodeError as e:
            last_error = e
            continue
    raise UnicodeDecodeError("glossary", b"", 0, 1,
                             f"could not decode {path}: {last_error}")


def _write_glossary_csv(path, header, rows):
    """Write header+rows as utf-8-sig CSV, dropping fully-empty rows. Returns the
    number of data rows written."""
    cleaned = [r for r in rows if "".join(str(c) for c in r).strip()]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(header)
        writer.writerows(cleaned)
    return len(cleaned)


def save_glossary(glossary_name, header, rows):
    """Write header+rows back as utf-8-sig CSV. Drops fully-empty rows and
    refuses to overwrite a non-empty file with an empty table (web guard)."""
    path = glossary_path(glossary_name)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Glossary not found: {glossary_name}")
    cleaned = [r for r in rows if "".join(str(c) for c in r).strip()]
    if not cleaned and os.path.getsize(path) > 0:
        raise ValueError("Refused: table is empty but the glossary file is not. Load it first.")
    return _write_glossary_csv(path, header, rows)


# Characters that are illegal in a Windows filename (the name becomes <name>.csv).
_GLOSSARY_BAD_CHARS = set('\\/:*?"<>|')


def _validate_new_glossary(glossary_name):
    """Resolve+validate a name for a NEW glossary. Returns the path. Raises
    ValueError for an invalid name and FileExistsError if it already exists."""
    name = (glossary_name or "").strip()
    if not name or _GLOSSARY_BAD_CHARS & set(name):
        raise ValueError(f"Invalid glossary name: {glossary_name!r}")
    path = glossary_path(name)
    if not path:
        raise ValueError(f"Invalid glossary name: {glossary_name!r}")
    if os.path.exists(path):
        raise FileExistsError(f"Glossary already exists: {name}")
    return path


def create_glossary(glossary_name, header=None):
    """Create a new, empty glossary (language-code header row only). Raises if the
    name is invalid or already taken."""
    path = _validate_new_glossary(glossary_name)
    os.makedirs(GLOSSARY_DIR, exist_ok=True)
    _write_glossary_csv(path, header or list(_DEFAULT_GLOSSARY_HEADER), [])
    return glossary_name.strip()


def import_glossary(glossary_name, src_path):
    """Create a new glossary from an existing CSV file: read it (encoding-detected)
    and re-write it normalized as utf-8-sig under ``glossary_name``."""
    path = _validate_new_glossary(glossary_name)
    rows = None
    for enc in _GLOSSARY_ENCODINGS:
        try:
            with open(src_path, "r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if rows is None:
        raise UnicodeDecodeError("glossary", b"", 0, 1,
                                 f"could not decode import file: {src_path}")
    os.makedirs(GLOSSARY_DIR, exist_ok=True)
    header = rows[0] if rows else list(_DEFAULT_GLOSSARY_HEADER)
    _write_glossary_csv(path, header, rows[1:] if len(rows) > 1 else [])
    return glossary_name.strip()


def delete_glossary(glossary_name):
    """Delete a glossary file. The built-in 'Default' is protected (it backs the
    default_glossary config). Raises FileNotFoundError if missing."""
    if glossary_name == "Default":
        raise ValueError("The Default glossary cannot be deleted.")
    path = glossary_path(glossary_name)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Glossary not found: {glossary_name}")
    os.remove(path)
    return glossary_name


# Live-caption glossary hint cache: (path, mtime, src, dst) -> parsed entries.
_live_gloss_cache = {"key": None, "entries": []}


def live_glossary_hint(source, src_lang, dst_lang, max_terms=8):
    """Terminology directive for LIVE translation: entries of the user's default
    glossary whose source term occurs in ``source``, rendered as a short hint for
    translate_text_simple's ``context`` parameter — so proper nouns stay as
    consistent in live captions as they are in document translation. Returns ""
    when nothing matches (the common case; costs one cached lookup + substring
    scan). Entries are cached per (glossary mtime, language pair)."""
    if not source:
        return ""
    try:
        path = glossary_path(get_config("default_glossary", "Default"))
        if not path or not os.path.exists(path):
            return ""
        key = (path, os.path.getmtime(path), src_lang or "auto", dst_lang)
        if _live_gloss_cache["key"] != key:
            from core.engine.text_separator import load_glossary as _load
            _live_gloss_cache["key"] = key
            _live_gloss_cache["entries"] = _load(path, src_lang or "auto", dst_lang) or []
        low = source.lower()
        hits = []
        for s, t in _live_gloss_cache["entries"]:
            if not s or not t:
                continue
            # ASCII terms match case-insensitively ("alice" spoken -> "Alice").
            if (s.lower() in low) if s.isascii() else (s in source):
                hits.append((s, t))
                if len(hits) >= max_terms:
                    break
        if not hits:
            return ""
        pairs = "; ".join(f"{s} => {t}" for s, t in hits)
        return f"Fixed terminology (use EXACTLY these translations): {pairs}"
    except Exception:  # noqa: BLE001 — a hint must never break live translation
        return ""


# --- Proofread (post-translation editing) -----------------------------------
# Pure reimplementation of app.py's proofread helpers (no Gradio). For the
# desktop single-user app the WHOLE temp tree (flat + one level deep) is the
# user's own, so there is no session-hash containment - but the path-traversal
# guard (realpath must stay inside temp) is preserved.

def _proofread_doc_dir(doc_name):
    """Resolve a proofread doc name to a folder strictly inside temp dir."""
    if not doc_name:
        return None
    temp_dir, _, _ = get_custom_paths()
    base = os.path.realpath(temp_dir)
    candidate = os.path.realpath(os.path.join(base, doc_name))
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    return candidate


def _is_finished_doc(folder):
    """True if folder has dst_translated.json + manifest.json (proofreadable).

    PDFs are included now: the PDF translator persists the same editable table,
    and export re-renders via BabelDOC from the edited text. Old PDFs translated
    before that change simply have no dst_translated.json, so they're filtered out
    here naturally."""
    if not os.path.exists(os.path.join(folder, "dst_translated.json")):
        return False
    return os.path.exists(os.path.join(folder, "manifest.json"))


def _proofread_doc_mtime(folder):
    """A doc's completion time = mtime of its dst_translated.json (falls back to
    the folder mtime). Used to sort the proofread list by 'time'."""
    try:
        p = os.path.join(folder, "dst_translated.json")
        return os.path.getmtime(p if os.path.exists(p) else folder)
    except OSError:
        return 0.0


def sort_proofread_docs(items, sort_by="time", descending=True):
    """Sort proofread docs. ``items`` is a list of (relname, folder_abspath).
    sort_by 'time' = completion time (default, newest first); 'name' = the doc's
    own name (last path component), A->Z by default. Returns the sorted relnames."""
    if sort_by == "name":
        keyed = sorted(items, key=lambda t: os.path.basename(t[0]).casefold(),
                       reverse=descending)
    else:
        keyed = sorted(items, key=lambda t: _proofread_doc_mtime(t[1]),
                       reverse=descending)
    return [name for name, _ in keyed]


def list_proofread_docs(sort_by="time", descending=True):
    """List finished, proofreadable docs anywhere in the temp tree.

    Walks the whole temp tree (bounded depth) and returns every folder that holds
    dst_translated.json + manifest.json, named by its path relative to temp (with
    forward slashes). A bounded recursive walk — instead of the old flat + 1-level
    scan — so BOTH frontends' layouts are found and SHARED in local single-user
    mode: Qt writes ``temp/<run-stamp>/<doc>`` (and ``…/<iso>/<doc>`` for same-name
    isolation) while the Web app writes ``temp/<session>/<task>/<doc>``. Without
    the deeper walk a Qt translation was invisible to Web's proofread list and
    vice-versa."""
    temp_dir, _, _ = get_custom_paths()
    base = os.path.realpath(temp_dir)
    docs = []   # (relname, folder_abspath) — folder kept for time-sorting

    def _walk(folder, rel, depth):
        if depth > 4:   # safety bound; real layouts are 1-3 deep
            return
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return
        for name in entries:
            sub = os.path.join(folder, name)
            if not os.path.isdir(sub):
                continue
            relname = f"{rel}/{name}" if rel else name
            if _is_finished_doc(sub):
                docs.append((relname, sub))   # a doc's own subfolders aren't separate docs
                continue
            _walk(sub, relname, depth + 1)

    _walk(base, "", 0)
    return sort_proofread_docs(docs, sort_by, descending)


def load_proofread_table(doc_name):
    """Load dst_translated.json as a list of (count_src, original, translated)
    tuples for the editable table. Raises FileNotFoundError if missing."""
    folder = _proofread_doc_dir(doc_name)
    dst_path = os.path.join(folder, "dst_translated.json") if folder else None
    if not dst_path or not os.path.exists(dst_path):
        raise FileNotFoundError(f"Translation data not found: {doc_name}")
    with open(dst_path, encoding="utf-8") as f:
        data = json.load(f)
    return [
        (item.get("count_src"), item.get("original", ""), item.get("translated", ""))
        for item in data
    ]


def save_proofread_table(doc_name, rows):
    """Write edited 'translated' values back into dst_translated.json.

    rows is a list of (count_src, original, translated). Only the translated
    field is updated. Validates the row count matches the file and (when
    count_src is intact) the row order. Refuses to wipe a non-empty file with
    all-empty translations (mirrors the glossary empty-over-nonempty guard).
    Returns the number of changed rows."""
    folder = _proofread_doc_dir(doc_name)
    dst_path = os.path.join(folder, "dst_translated.json") if folder else None
    if not dst_path or not os.path.exists(dst_path):
        raise FileNotFoundError(f"Translation data not found: {doc_name}")
    with open(dst_path, encoding="utf-8") as f:
        data = json.load(f)
    if rows is None or len(rows) != len(data):
        got = 0 if rows is None else len(rows)
        raise ValueError(
            f"Row count mismatch: expected {len(data)}, got {got}. Edits not saved.")

    # Refuse to overwrite non-empty translations with an all-empty table
    had_any = any(str(item.get("translated", "")).strip() for item in data)
    now_any = any(str(r[2]).strip() for r in rows)
    if had_any and not now_any:
        raise ValueError(
            "Refused: all translations are empty but the file is not. Load it first.")

    changed = 0
    for i, item in enumerate(data):
        # Guard against reordered rows when count_src is intact
        try:
            if int(rows[i][0]) != int(item.get("count_src")):
                raise ValueError(
                    f"Row count mismatch: expected {len(data)}, got {len(rows)}. "
                    "Edits not saved.")
        except (TypeError, ValueError) as e:
            if isinstance(e, ValueError) and "mismatch" in str(e):
                raise
        new_val = rows[i][2]
        new_val = "" if new_val is None else str(new_val)
        if new_val != item.get("translated", ""):
            item["translated"] = new_val
            changed += 1
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return changed


def _export_pdf_proofread(folder, manifest, doc_name, dst_json, original_copy):
    """Re-render a PDF from the EDITED translations via BabelDOC (no LLM call).

    Reads dst_translated.json -> {source: edited}, then runs one BabelDOC pass
    that returns the edited text per paragraph. Returns the regenerated PDF path."""
    if not os.path.exists(dst_json) or not os.path.exists(original_copy):
        raise FileNotFoundError(f"Translation data not found: {doc_name}")
    with open(dst_json, encoding="utf-8") as f:
        data = json.load(f)
    overrides = {item.get("original", ""): item.get("translated", "")
                 for item in data if item.get("original")}
    from core.translators.pdf_translator import PdfTranslator
    temp_dir, result_dir, log_dir = get_custom_paths()
    bilingual = bool(manifest.get("bilingual_mode", False))
    translator = PdfTranslator(
        original_copy, manifest.get("model", ""), False, "",
        manifest.get("src_lang", "en"), manifest.get("dst_lang", "en"), False,
        max_token=768, max_retries=1, thread_count=4, glossary_path=None,
        temp_dir=temp_dir, result_dir=result_dir, session_lang="en", log_dir=log_dir,
        word_bilingual_mode=bilingual,
    )
    produced = translator.reexport_proofread(overrides)
    src_lang_code = manifest.get("src_lang", "en")
    dst_lang_code = manifest.get("dst_lang", "en")
    doc_leaf = os.path.basename(doc_name.replace("/", os.sep))
    final_path = os.path.join(
        result_dir, f"{doc_leaf}_{src_lang_code}2{dst_lang_code}{_proofread_suffix()}.pdf")
    os.replace(produced, final_path)
    return final_path


def _proofread_suffix():
    """Unique output suffix for a proofread re-export. A FIXED name (…_proofread.x)
    can't be overwritten while the user has the previous export open (Windows file
    lock) — which made re-export "work only once". A timestamp makes every click
    write a fresh file, so re-export always succeeds and prior versions are kept."""
    from datetime import datetime
    return f"_proofread_{datetime.now().strftime('%H%M%S_%f')[:-3]}"


def _export_manga_pdf_proofread(folder, manifest, doc_name):
    """Re-render a manga-mode PDF from EDITED translations: draw the edited text
    onto the cached page images (manga_pages.json) and repack to a PDF. No LLM,
    no OCR — just re-render + repack."""
    from core.translators.manga_pdf_translator import render_manga_pages_to_pdf
    with open(os.path.join(folder, "manga_pages.json"), encoding="utf-8") as f:
        pages_meta = json.load(f)
    with open(os.path.join(folder, "dst_translated.json"), encoding="utf-8") as f:
        translations = {item["count_src"]: item["translated"] for item in json.load(f)}
    _, result_dir, _ = get_custom_paths()
    src = manifest.get("src_lang", "en")
    dst = manifest.get("dst_lang", "en")
    name = os.path.basename(doc_name.replace("/", os.sep))
    # Unique suffix: separate from the original translated PDF AND re-exportable
    # repeatedly even if a previous export is open (no fixed-name file lock).
    return render_manga_pages_to_pdf(
        pages_meta, translations, folder, result_dir, dst, name, src,
        out_suffix=_proofread_suffix())


def export_proofread_doc(doc_name):
    """Re-export the document from the (edited) dst_translated.json using the
    original-format writer and the copied original file. Returns the absolute
    path of the regenerated file. Raises on failure."""
    folder = _proofread_doc_dir(doc_name)
    if not folder or not os.path.isdir(folder):
        raise FileNotFoundError(f"Translation data not found: {doc_name}")
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    ext = str(manifest.get("file_extension", "")).lower()
    src_json = os.path.join(folder, "src.json")
    dst_json = os.path.join(folder, "dst_translated.json")
    original_copy = os.path.join(
        folder, manifest.get("original_copy", f"{os.path.basename(doc_name)}{ext}"))

    # PDF re-export re-renders via BabelDOC from the edited translations (it has
    # no in-place text writer like the other formats), so it takes its own path.
    # A manga-mode PDF instead re-renders the page images (manga_pages.json) and
    # repacks — detected by the presence of that file.
    if ext == ".pdf":
        if os.path.exists(os.path.join(folder, "manga_pages.json")):
            return _export_manga_pdf_proofread(folder, manifest, doc_name)
        return _export_pdf_proofread(folder, manifest, doc_name, dst_json, original_copy)

    for required in (src_json, dst_json, original_copy):
        if not os.path.exists(required):
            raise FileNotFoundError(f"Translation data not found: {doc_name}")

    bilingual = bool(manifest.get("bilingual_mode", False))
    translator_class = get_translator_class(
        ext,
        excel_mode_2=bool(manifest.get("use_xlwings", False)),
        word_bilingual_mode=bilingual,
        excel_bilingual_mode=bilingual,
        subtitle_bilingual_mode=bilingual,
        txt_bilingual_mode=bilingual,
        md_bilingual_mode=bilingual,
        epub_bilingual_mode=bilingual,
        html_bilingual_mode=bilingual,
    )
    if not translator_class:
        raise ValueError(f"Unsupported file type '{ext}'.")

    temp_dir, result_dir, log_dir = get_custom_paths()
    translator = translator_class(
        original_copy, manifest.get("model", ""), False, "",
        manifest.get("src_lang", "en"), manifest.get("dst_lang", "en"), False,
        max_token=768, max_retries=1, thread_count=1, glossary_path=None,
        temp_dir=temp_dir, result_dir=result_dir,
        session_lang="en", log_dir=log_dir,
    )
    translator.write_translated_json_to_file(src_json, dst_json)

    src_lang_code = manifest.get("src_lang", "en")
    dst_lang_code = manifest.get("dst_lang", "en")
    copy_base = os.path.splitext(os.path.basename(original_copy))[0]
    produced = os.path.join(result_dir, f"{copy_base}_{src_lang_code}2{dst_lang_code}{ext}")
    if not os.path.exists(produced):
        raise RuntimeError(f"Export produced no file ({produced})")
    doc_leaf = os.path.basename(doc_name.replace("/", os.sep))
    final_path = os.path.join(
        result_dir, f"{doc_leaf}_{src_lang_code}2{dst_lang_code}{_proofread_suffix()}{ext}")
    os.replace(produced, final_path)
    return final_path


# --- Multi-file results packaging -------------------------------------------
def zip_results(output_paths, file_results, dest_dir=None):
    """Zip the given output files together with a results.txt per-file status
    report (mirrors app.process_multiple_files). ``file_results`` is a list of
    (file_name, status, detail). Returns the zip path."""
    import zipfile

    if dest_dir is None:
        _, dest_dir, _ = get_custom_paths()
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "translated_files.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        used_names = set()
        for p in output_paths:
            if p and os.path.exists(p):
                # Uniquify on basename collision so two results with the same name
                # don't overwrite each other in the zip (the 2nd would be lost).
                arc = os.path.basename(p)
                if arc in used_names:
                    stem, ext = os.path.splitext(arc)
                    i = 2
                    while f"{stem} ({i}){ext}" in used_names:
                        i += 1
                    arc = f"{stem} ({i}){ext}"
                used_names.add(arc)
                with open(p, "rb") as fh:   # close the handle (was leaked)
                    zf.writestr(arc, fh.read())
        lines = []
        for name, status, detail in file_results:
            line = f"{name}: {status}"
            if detail:
                line += f" - {detail}"
            lines.append(line)
        zf.writestr("results.txt", "\n".join(lines) + "\n")
    return zip_path


# --- Language helpers -------------------------------------------------------
def available_languages():
    return get_available_languages()


def language_code(display_name):
    return get_language_code(display_name)


def labels_for(lang="en"):
    return LABEL_TRANSLATIONS.get(lang, LABEL_TRANSLATIONS["en"])
