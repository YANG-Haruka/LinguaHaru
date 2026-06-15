# pipeline/subtitle_formats_pipeline.py
# Additional subtitle formats: WebVTT (.vtt), Advanced SubStation (.ass/.ssa)
# and lyrics (.lrc). Each extracts cue text for the standard pipeline and
# reconstructs the file with everything else (timestamps, settings, styles,
# override tags) byte-preserved.
import json
import os
import re

from .skip_pipeline import should_translate
from .txt_translation_pipeline import read_file_with_encoding
from core.log_config import app_logger

# Hours are optional in WebVTT: both HH:MM:SS.mmm and MM:SS.mmm are valid.
_VTT_TIMESTAMP = re.compile(r"(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s*-->")
_LRC_TIME_TAGS = re.compile(r"^((?:\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\])+)(.*)$")
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")


def _save_extraction(file_path, temp_dir, content_data, layout):
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    with open(os.path.join(temp_folder, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)
    return json_path


def _load_for_writeback(file_path, temp_dir, original_json_path, translated_json_path):
    filename = os.path.splitext(os.path.basename(file_path))[0]
    with open(os.path.join(temp_dir, filename, "layout.json"), encoding="utf-8") as f:
        layout = json.load(f)
    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)
    translations = {item["count_src"]: item["translated"] for item in translated_data}
    return layout, original_data, translations


def _result_path(file_path, result_dir, src_lang, dst_lang, extension=None):
    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    filename = os.path.splitext(os.path.basename(file_path))[0]
    extension = extension or os.path.splitext(file_path)[1].lower()
    return os.path.join(result_dir, f"{filename}_{lang_suffix}{extension}")


# ------------------------------------------------------------------ VTT ----
def extract_vtt_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    content_data = []
    count = 0
    line_map = {}  # output line index -> count_src
    in_cue = False
    in_note = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            in_cue = False
            in_note = False
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            in_note = True
            continue
        if _VTT_TIMESTAMP.match(stripped):
            in_cue = True
            continue
        if in_cue and not in_note:
            if should_translate(stripped):
                count += 1
                content_data.append({"count_src": count, "type": "text", "value": line})
                line_map[str(i)] = count

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "line_map": line_map})
    app_logger.info(f"VTT: extracted {count} cue lines")
    return json_path


def write_translated_content_to_vtt(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None,
                                    bilingual_mode=False):
    layout, _, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)

    lines = layout["lines"]
    for line_index, count in layout["line_map"].items():
        translated = translations.get(count)
        if translated:
            translated = translated.replace("␊", "\n").replace("␍", "")
            original = lines[int(line_index)]
            if bilingual_mode and translated.strip() != original.strip():
                # Bilingual cue: translation first, original below
                translated = f"{translated}\n{original}"
            lines[int(line_index)] = translated

    result_path = _result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    app_logger.info(f"Translated VTT saved to: {result_path}")
    return result_path


# ------------------------------------------------------------------ ASS ----
def extract_ass_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    content_data = []
    count = 0
    cue_info = {}   # line index -> {count_src, prefix, tags}
    in_events = False
    text_field_index = 9  # default per spec

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("[events]"):
            in_events = True
            continue
        if stripped.startswith("["):
            in_events = False
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
            if "text" in fields:
                text_field_index = fields.index("text")
            continue
        if not stripped.lower().startswith("dialogue:"):
            continue

        head, body = line.split(":", 1)
        parts = body.split(",", text_field_index)
        if len(parts) <= text_field_index:
            continue
        text = parts[text_field_index]

        # Protect override tags {\...} with double-brace markers (covered by
        # the placeholder-survival check), and \N line breaks with ␊
        tags = _ASS_OVERRIDE.findall(text)
        protected = text
        for tag_index, tag in enumerate(tags):
            protected = protected.replace(tag, f"{{{{ASS_{tag_index}}}}}", 1)
        protected = protected.replace("\\N", "␊").replace("\\n", "␊")

        plain = _ASS_OVERRIDE.sub("", text).replace("\\N", " ").replace("\\n", " ").strip()
        if not plain or not should_translate(plain):
            continue

        count += 1
        content_data.append({"count_src": count, "type": "text", "value": protected})
        cue_info[str(i)] = {
            "count_src": count,
            "prefix": head + ":" + ",".join(parts[:text_field_index]) + ",",
            "tags": tags,
        }

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "cue_info": cue_info})
    app_logger.info(f"ASS: extracted {count} dialogue lines")
    return json_path


def write_translated_content_to_ass(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    layout, _, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)

    lines = layout["lines"]
    for line_index, info in layout["cue_info"].items():
        translated = translations.get(info["count_src"])
        if not translated:
            continue
        text = translated.replace("␊", "\\N").replace("␍", "")
        for tag_index, tag in enumerate(info["tags"]):
            text = text.replace(f"{{{{ASS_{tag_index}}}}}", tag)
        lines[int(line_index)] = info["prefix"] + text

    result_path = _result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    app_logger.info(f"Translated ASS saved to: {result_path}")
    return result_path


# ------------------------------------------------------------------ LRC ----
def extract_lrc_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    content_data = []
    count = 0
    line_info = {}  # line index -> {count_src, prefix}

    for i, line in enumerate(lines):
        match = _LRC_TIME_TAGS.match(line.strip())
        if not match:
            continue  # metadata ([ti:...]) and plain lines stay untouched
        prefix, text = match.group(1), match.group(2).strip()
        if not text or not should_translate(text):
            continue
        count += 1
        content_data.append({"count_src": count, "type": "text", "value": text})
        line_info[str(i)] = {"count_src": count, "prefix": prefix}

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "line_info": line_info})
    app_logger.info(f"LRC: extracted {count} lyric lines")
    return json_path


def write_translated_content_to_lrc(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    layout, _, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)

    lines = layout["lines"]
    for line_index, info in layout["line_info"].items():
        translated = translations.get(info["count_src"])
        if translated:
            lines[int(line_index)] = info["prefix"] + translated.replace("␊", " ")

    result_path = _result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    app_logger.info(f"Translated LRC saved to: {result_path}")
    return result_path
