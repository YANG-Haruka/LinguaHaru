import json
import os
import re
from .txt_translation_pipeline import read_file_with_encoding
from .subtitle_formats_pipeline import subtitle_result_path
from .subtitle_groups import (
    drop_fabricated_tags, flat, group_cues, join_texts, seconds, split_across_cues)
from core.log_config import app_logger


def _read_srt_text(file_path):
    """Read an SRT as text, tolerant of the encoding it was saved in.

    utf-8-sig first: it strips a leading BOM (a plain-utf-8 read glues the BOM
    onto the first cue index '﻿1', breaking that cue) and covers plain UTF-8.
    If that fails the file isn't UTF-8 — commonly a GBK/GB2312/Big5/Shift-JIS
    subtitle from a local player or fansub (byte 0xd5 etc.) — so fall back to
    chardet + the common-encoding ladder shared with the txt pipeline. The BOM
    strip guards a UTF-16 file whose detected codec leaves the mark in place."""
    try:
        with open(file_path, "r", encoding="utf-8-sig") as file:
            return file.read()
    except UnicodeDecodeError:
        content, enc = read_file_with_encoding(file_path)
        app_logger.info(f"SRT not UTF-8; read with detected encoding: {enc}")
        return content.lstrip("﻿")


_SRT_MARKUP = re.compile(r"<[^>]*>|\{")


def _srt_info(cue):
    # A cue with markup (<i>, <font>, {\an8}) or several lines stays its own unit:
    # a cut could split a tag pair, and a grouped cue would lose its line breaks.
    v = cue["value"]
    solo = bool(_SRT_MARKUP.search(v)) or "␊" in v or "␍" in v
    return (flat(v), seconds(cue["start_time"]), seconds(cue["end_time"]), solo, None)


def extract_srt_content_to_json(file_path, temp_dir):
    """
    Extract subtitles from an SRT file and save them in a JSON format.
    """
    srt_content = _read_srt_text(file_path)

    # Tolerant of common SRT variants: 1-2 digit hours, '.' as millisecond
    # separator, 1-3 millisecond digits. The numeric index line is optional
    # (some files omit it); the timestamp line is the reliable anchor, and we
    # renumber sequentially anyway.
    srt_pattern = re.compile(
        r"(?:\d+\s*\r?\n)?"
        r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"      # 1-3 digit hours
        r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
        r"([^\r\n]*)\r?\n"                                # trailing position coords (X1:.. Y2:..) preserved
        r"(.*?)(?=\r?\n\r?\n|\Z)",
        re.DOTALL
    )

    cues = []
    for match in srt_pattern.finditer(srt_content):
        start_time, end_time, extra, value = match.groups()
        cues.append({
            "start_time": start_time.replace(".", ","),
            "end_time": end_time.replace(".", ","),
            "extra_time": (extra or "").rstrip(),   # e.g. " X1:100 X2:200 Y1:50 Y2:80"
            # rstrip: the last cue would otherwise keep the file's trailing
            # newline as a ␊ marker the model must echo or the cue fails
            "value": value.rstrip("\r\n").replace("\n", "␊").replace("\r", "␍"),
        })

    # Cues that don't match the pattern are silently absent from the output
    # file, so make the loss visible
    cue_count = srt_content.count("-->")
    if cue_count != len(cues):
        app_logger.warning(
            f"SRT parse mismatch: file contains {cue_count} cues but only "
            f"{len(cues)} were extracted; unparsed cues will be missing from the output"
        )

    # One translation unit per SENTENCE GROUP, not per cue. Real captions split a
    # sentence over 2-3 timed cues; sent as separate ids the model merges the
    # next cue's half-sentence into the current one and every later id drifts
    # (filled at the end with an invented "[Music]" or a repeated line). A unit
    # that is a whole sentence leaves nothing to merge; the writer splits its
    # translation back over the group's cues. count_src (renumbered, so repeated
    # cue numbers can't collapse) is the group id.
    content_data = []
    for idx, group in enumerate(group_cues(cues, _srt_info), start=1):
        content_data.append({
            "count_src": idx,
            "start_time": group[0]["start_time"],
            "end_time": group[-1]["end_time"],
            "extra_time": group[0]["extra_time"],
            # A lone cue keeps its line markers; a multi-cue group (single-line
            # cues only, see _srt_info) is joined into one line of text.
            "value": group[0]["value"] if len(group) == 1
                     else join_texts([flat(c["value"]) for c in group]),
            "cues": group,
        })
    
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    return json_path

def write_translated_content_to_srt(file_path, original_json_path, translated_json_path, result_dir, src_lang=None, dst_lang=None, bilingual_mode=False):
    """
    Write translated content back to the SRT file while keeping timestamps intact.

    Args:
        src_lang: Source language code (e.g., 'zh')
        dst_lang: Target language code (e.g., 'ja')
        bilingual_mode: If True, each cue contains the translation followed by
                        the original text (standard bilingual subtitle layout)
    """
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    translations = {str(item["count_src"]): item["translated"] for item in translated_data}

    output_srt_lines = []
    count = 0

    for item in original_data:
        value = item["value"]
        translated_text = drop_fabricated_tags(value, translations.get(str(item["count_src"]), value))
        # Older src.json (history / proofread re-export) has one item per cue.
        group = item.get("cues") or [item]
        if len(group) == 1:
            pieces = [translated_text]
        else:
            pieces = split_across_cues(value, [flat(c["value"]) for c in group], translated_text)

        for cue, piece in zip(group, pieces):
            count += 1
            piece = piece.replace("␊", "\n").replace("␍", "\r")
            if bilingual_mode:
                original_text = cue["value"].replace("␊", "\n").replace("␍", "\r")
                if piece.strip() != original_text.strip():
                    from core.engine.bilingual_format import style_markup
                    piece = f"{style_markup(piece, 'srt')}\n{original_text}"
            extra = cue.get("extra_time", "")   # preserved position coords, if any
            ts_line = f"{cue['start_time']} --> {cue['end_time']}" + (f" {extra}" if extra else "")
            output_srt_lines.append(f"{count}\n{ts_line}\n{piece}\n\n")

    result_path = subtitle_result_path(file_path, result_dir, src_lang, dst_lang, ".srt")
    
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.writelines(output_srt_lines)
    
    app_logger.info(f"Translated SRT file saved to: {result_path}")
    return result_path
