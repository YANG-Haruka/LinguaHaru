# pipeline/json_translation_pipeline.py
# JSON translation (i18n/locale files and similar key-value data): every
# string VALUE that passes the skip filter is translated; keys, numbers,
# booleans and structure stay untouched.
#
# Key/path exclusion (additive): string values whose IMMEDIATE dict key is in
# the configured skip set (json_skip_keys, case-insensitive) are left untouched,
# even if their content looks translatable. List/array elements inherit the
# nearest enclosing dict key. An optional json_skip_paths list (dotted paths,
# e.g. "config.api.endpoint") excludes precise locations.
import json
import os

from .skip_pipeline import should_translate
from .txt_translation_pipeline import read_file_with_encoding
from core.log_config import app_logger
from core.paths import SYSTEM_CONFIG

# Keys whose values are machine identifiers / formatting, not human content.
DEFAULT_SKIP_KEYS = {
    "id", "_id", "uuid", "guid", "key", "keybinding", "url", "uri", "href",
    "src", "link", "regex", "pattern", "mime", "mimetype", "locale", "lang",
    "language", "code", "hash", "sha", "md5", "token", "slug", "class",
    "classname", "style", "color", "colour", "icon", "path", "ref", "version",
}


def _load_skip_config():
    """Return (skip_keys:set[str lower], skip_paths:set[str]) from system_config."""
    skip_keys = DEFAULT_SKIP_KEYS
    skip_paths = set()
    try:
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg.get("json_skip_keys"), list):
            skip_keys = {str(k).lower() for k in cfg["json_skip_keys"]}
        if isinstance(cfg.get("json_skip_paths"), list):
            skip_paths = {str(p) for p in cfg["json_skip_paths"]}
    except Exception:
        pass
    return {k.lower() for k in skip_keys}, skip_paths


def _dotted_path(path):
    """Dotted form of a path, ignoring list indices: ['a', 0, 'b'] -> 'a.b'."""
    return ".".join(str(step) for step in path if not isinstance(step, int))


def _walk_strings(node, path, visit, key_context, skip_keys, skip_paths):
    """key_context = the nearest enclosing dict key (inherited by list elements)."""
    if isinstance(node, dict):
        for key, value in node.items():
            _walk_strings(value, path + [key], visit, key, skip_keys, skip_paths)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_strings(value, path + [index], visit, key_context, skip_keys, skip_paths)
    elif isinstance(node, str):
        if key_context is not None and str(key_context).lower() in skip_keys:
            return
        if skip_paths and _dotted_path(path) in skip_paths:
            return
        visit(path, node)


def extract_json_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    data = json.loads(content)

    content_data = []
    counter = {"n": 0}
    skip_keys, skip_paths = _load_skip_config()

    def visit(path, value):
        text = value.strip()
        if not text or not should_translate(text):
            return
        counter["n"] += 1
        content_data.append({
            "count_src": counter["n"],
            "type": "text",
            "value": value.replace("\n", "␊").replace("\r", "␍"),
            "path": path,
        })

    _walk_strings(data, [], visit, None, skip_keys, skip_paths)

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)

    app_logger.info(f"JSON: extracted {counter['n']} string values")
    return json_path


def write_translated_content_to_json(file_path, original_json_path, translated_json_path,
                                     temp_dir, result_dir, src_lang=None, dst_lang=None):
    content, _ = read_file_with_encoding(file_path)
    data = json.loads(content)

    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}

    for item in original_data:
        translated = translations.get(item["count_src"])
        if not translated:
            continue
        translated = translated.replace("␊", "\n").replace("␍", "\r")
        target = data
        path = item["path"]
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = translated

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}.json")

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    app_logger.info(f"Translated JSON saved to: {result_path}")
    return result_path
