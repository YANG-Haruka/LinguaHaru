"""Backend glue for the Qt desktop app. NO Qt imports here.

Everything the UI needs that touches the LinguaHaru translation backend or its
config lives here: extension -> translator-class resolution (with the same
bilingual partial() logic as app.py), system_config read/write helpers, model
list discovery, glossary list/load/save, and language helpers.

This module deliberately does NOT import app.py (that builds the whole Gradio
web UI). The extension->class map and the per-extension bilingual partial()
logic below are copied faithfully from app.get_translator_class.
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

# Cap on concurrent file translations (mirrors app.MAX_CONCURRENT_TASKS).
MAX_CONCURRENT_TASKS = 3

# --- Extension -> translator module (mirrors app.TRANSLATOR_MODULES) ---------
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


def accepted_extensions():
    """All extensions the UI should accept in the file picker (sorted)."""
    return sorted(TRANSLATOR_MODULES.keys())


def get_translator_class(
    file_extension,
    excel_mode_2=False, word_bilingual_mode=False, excel_bilingual_mode=False,
    pdf_bilingual_mode=False, subtitle_bilingual_mode=False, txt_bilingual_mode=False,
    md_bilingual_mode=False, epub_bilingual_mode=False, html_bilingual_mode=False,
):
    """Import and return the translator class (or a partial with the bilingual
    knobs bound) for a file extension. Copied from app.get_translator_class."""
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
    "result_dir": "data/result",
    "log_dir": "data/log",
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
    """Resolve and create temp/result/log dirs (absolute, anchored at repo)."""
    config = read_config()
    paths = []
    for key, default in (("temp_dir", "data/temp"), ("result_dir", "data/result"), ("log_dir", "data/log")):
        d = config.get(key, default)
        if not os.path.isabs(d):
            d = os.path.join(RUNTIME_ROOT, d)   # writable runtime root, not bundle
        os.makedirs(d, exist_ok=True)
        paths.append(d)
    return tuple(paths)


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
    for a document => less rate-limit/429 pressure + better context."""
    if model:
        mc = read_api_config(model) or {}
        mt = mc.get("max_token")
        if mt:
            try:
                return max(128, int(mt))
            except (TypeError, ValueError):
                pass
    return read_config().get("max_token", 4096)


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
# User-editable -> writable runtime data dir (not the read-only bundle).
GLOSSARY_DIR = os.path.join(DATA_DIR, "glossary")
_GLOSSARY_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "shift-jis")


def get_glossary_files():
    """Glossary names (no .csv), Default first."""
    os.makedirs(GLOSSARY_DIR, exist_ok=True)
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


def save_glossary(glossary_name, header, rows):
    """Write header+rows back as utf-8-sig CSV. Drops fully-empty rows and
    refuses to overwrite a non-empty file with an empty table (web guard)."""
    path = glossary_path(glossary_name)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Glossary not found: {glossary_name}")
    cleaned = [r for r in rows if "".join(str(c) for c in r).strip()]
    if not cleaned and os.path.getsize(path) > 0:
        raise ValueError("Refused: table is empty but the glossary file is not. Load it first.")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if header:
            writer.writerow(header)
        writer.writerows(cleaned)
    return len(cleaned)


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
    """True if folder has dst_translated.json + manifest.json and is not PDF."""
    if not os.path.exists(os.path.join(folder, "dst_translated.json")):
        return False
    manifest_path = os.path.join(folder, "manifest.json")
    if not os.path.exists(manifest_path):
        return False
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return False
    # PDF re-export would re-run the whole BabelDOC pass; exclude it
    return str(manifest.get("file_extension", "")).lower() != ".pdf"


def list_proofread_docs():
    """List finished, proofreadable docs across the whole temp tree.

    Scans flat legacy docs (temp/<doc>) and one level deep (temp/<sub>/<doc>),
    requiring dst_translated.json + manifest.json and excluding PDF."""
    temp_dir, _, _ = get_custom_paths()
    docs = []
    try:
        for name in sorted(os.listdir(temp_dir)):
            folder = os.path.join(temp_dir, name)
            if not os.path.isdir(folder):
                continue
            if _is_finished_doc(folder):
                docs.append(name)
                continue
            # One level deep (session-id style subdirs)
            for sub in sorted(os.listdir(folder)):
                subfolder = os.path.join(folder, sub)
                if os.path.isdir(subfolder) and _is_finished_doc(subfolder):
                    docs.append(f"{name}/{sub}")
    except OSError:
        pass
    return docs


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
        result_dir, f"{doc_leaf}_{src_lang_code}2{dst_lang_code}_proofread{ext}")
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
