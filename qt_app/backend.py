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

# Cap on concurrent file translations (mirrors app.MAX_CONCURRENT_TASKS).
MAX_CONCURRENT_TASKS = 3

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
    "qt_ui_lang": "en",
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
        for p in output_paths:
            if p and os.path.exists(p):
                zf.writestr(os.path.basename(p), open(p, "rb").read())
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
