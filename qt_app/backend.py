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

from config.optional_modules import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS
from config.languages_config import (
    get_language_code, get_available_languages, LANGUAGE_MAP, LABEL_TRANSLATIONS,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- Extension -> translator module (mirrors app.TRANSLATOR_MODULES) ---------
TRANSLATOR_MODULES = {
    ".docx": "translator.word_translator.WordTranslator",
    ".pptx": "translator.ppt_translator.PptTranslator",
    ".xlsx": "translator.excel_translator.ExcelTranslator",
    ".pdf": "translator.pdf_translator.PdfTranslator",
    ".srt": "translator.subtitle_translator.SubtitlesTranslator",
    ".txt": "translator.txt_translator.TxtTranslator",
    ".md": "translator.md_translator.MdTranslator",
    ".epub": "translator.epub_translator.EpubTranslator",
    ".csv": "translator.csv_translator.CsvTranslator",
    ".tsv": "translator.csv_translator.CsvTranslator",
    ".html": "translator.extra_formats_translator.HtmlTranslator",
    ".htm": "translator.extra_formats_translator.HtmlTranslator",
    ".odt": "translator.extra_formats_translator.OdtTranslator",
    ".json": "translator.extra_formats_translator.JsonTranslator",
    ".vtt": "translator.extra_formats_translator.VttTranslator",
    ".ass": "translator.extra_formats_translator.AssTranslator",
    ".ssa": "translator.extra_formats_translator.AssTranslator",
    ".lrc": "translator.extra_formats_translator.LrcTranslator",
}
for _ext in IMAGE_EXTENSIONS:
    TRANSLATOR_MODULES[_ext] = "translator.image_translator.ImageTranslator"
for _ext in MEDIA_EXTENSIONS:
    TRANSLATOR_MODULES[_ext] = "translator.video_translator.VideoTranslator"


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
    except (ImportError, AttributeError):
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
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "system_config.json")

_DEFAULT_CONFIG = {
    "lan_mode": False,
    "default_online": False,
    "max_token": 768,
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
    "rpm_limit": 0,
    "qt_theme": "light",
    "temp_dir": "temp",
    "result_dir": "result",
    "log_dir": "log",
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


def get_custom_paths():
    """Resolve and create temp/result/log dirs (absolute, anchored at repo)."""
    config = read_config()
    paths = []
    for key, default in (("temp_dir", "temp"), ("result_dir", "result"), ("log_dir", "log")):
        d = config.get(key, default)
        if not os.path.isabs(d):
            d = os.path.join(REPO_ROOT, d)
        os.makedirs(d, exist_ok=True)
        paths.append(d)
    return tuple(paths)


def thread_count_for_mode(use_online):
    config = read_config()
    if use_online:
        return config.get("default_thread_count_online", 2)
    return config.get("default_thread_count_offline", 4)


# --- Model list discovery (mirrors app.py startup logic) ---------------------
def scan_online_models():
    """Online models = config/api_config/*.json filenames (without .json)."""
    config_dir = os.path.join(REPO_ROOT, "config", "api_config")
    try:
        return sorted(
            os.path.splitext(f)[0] for f in os.listdir(config_dir)
            if f.endswith(".json") and f != "Custom.json"
        )
    except OSError:
        return []


def scan_local_models(force_refresh=False):
    """Local model list via the offline LLM wrapper (Ollama, etc.)."""
    from llmWrapper.offline_translation import populate_sum_model
    return populate_sum_model(force_refresh=force_refresh) or []


def fetch_online_models(selected_model, api_key):
    """Query the selected config's base_url for its model list, writing new
    '(Fetched) <id>.json' configs. Returns (models, status_message)."""
    from llmWrapper.online_translation import fetch_models_into_configs
    added, error = fetch_models_into_configs(selected_model, api_key)
    models = scan_online_models()
    if error:
        return models, f"Fetch failed: {error} (kept {len(models)} entries)"
    return models, f"Refreshed: {added} new model(s), {len(models)} total."


# --- Glossary (CSV, utf-8-sig, multi-encoding read) -------------------------
GLOSSARY_DIR = os.path.join(REPO_ROOT, "glossary")
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


# --- Language helpers -------------------------------------------------------
def available_languages():
    return get_available_languages()


def language_code(display_name):
    return get_language_code(display_name)


def labels_for(lang="en"):
    return LABEL_TRANSLATIONS.get(lang, LABEL_TRANSLATIONS["en"])
