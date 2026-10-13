# pipeline/subtitle_formats_pipeline.py
# Additional subtitle formats: WebVTT (.vtt), Advanced SubStation (.ass/.ssa)
# and lyrics (.lrc). Each extracts cue text for the standard pipeline and
# reconstructs the file with everything else (timestamps, settings, styles,
# override tags) byte-preserved.
import json
import os
import re

from .skip_pipeline import should_translate
from .subtitle_groups import (
    drop_fabricated_tags, flat, group_cues, join_texts, seconds, split_across_cues)
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


def _match_source_name_enabled():
    """Config `subtitle_match_source_name` (default ON)."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("subtitle_match_source_name", True))
    except Exception:  # noqa: BLE001
        return True


def subtitle_result_path(file_path, result_dir, src_lang, dst_lang, extension=None):
    """Where a translated subtitle lands. Shared with the SRT pipeline.

    A player only auto-loads a subtitle that carries the video's own name
    (`movie.mp4` -> `movie.srt`), so that is the default; the result folder then
    holds ONE subtitle per video and a second run for another language replaces
    it. Unticking the option restores the `_<src>2<dst>` suffix, which keeps
    several languages side by side.
    """
    os.makedirs(result_dir, exist_ok=True)
    filename = os.path.splitext(os.path.basename(file_path))[0]
    extension = extension or os.path.splitext(file_path)[1].lower()
    if _match_source_name_enabled():
        return os.path.join(result_dir, f"{filename}{extension}")
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    return os.path.join(result_dir, f"{filename}_{lang_suffix}{extension}")


# ------------------------------------------------------------------ VTT ----
_VTT_TIMES = re.compile(r"((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d+:)?\d{2}:\d{2}[.,]\d{3})")
_MARKUP = re.compile(r"<[^>]*>|\{")


def extract_vtt_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    cues = []          # {"lines": [text line indices], "start": s, "end": e}
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
            times = _VTT_TIMES.match(stripped)
            cues.append({"lines": [],
                         "start": seconds(times.group(1)) if times else None,
                         "end": seconds(times.group(2)) if times else None})
            in_cue = True
            continue
        if in_cue and not in_note:
            cues[-1]["lines"].append(i)

    for cue in cues:
        idx = cue["lines"]
        cue["translatable"] = [i for i in idx if should_translate(lines[i].strip())]
        cue["text"] = "\n".join(lines[i] for i in idx)
        # Voice spans (<v Name>) mark distinct speakers -> keep per line so two
        # speakers are never merged. Also per line when a NON-translatable line
        # is sandwiched between translatable ones: grouping would drop the tail
        # and reorder the kept lines.
        cue["per_line"] = (any(lines[i].strip().startswith("<v") for i in idx)
                           or not cue["translatable"] or cue["translatable"] != idx)
        # Cues with inline tags (<i>, <c.x>, <00:01.000>) are never merged with
        # neighbours, so a cut can't separate a tag from its text.
        cue["solo"] = (cue["per_line"] or cue["start"] is None
                       or bool(_MARKUP.search(cue["text"])))

    content_data = []
    count = 0
    line_map = {}      # output line index -> count_src (first/only line of a unit)
    drop_lines = []    # extra cue lines merged into the first line's unit
    groups = {}        # count_src -> [{"line", "text"}] for a multi-cue sentence

    def add(value):
        nonlocal count
        count += 1
        content_data.append({"count_src": count, "type": "text", "value": value})
        return count

    def vtt_info(c):
        return flat(c["text"]), c["start"] or 0, c["end"] or 0, c["solo"], None

    for group in group_cues(cues, vtt_info):
        if len(group) > 1:
            n = add(join_texts([flat(c["text"]) for c in group]))
            groups[str(n)] = [{"line": c["lines"][0], "text": c["text"]} for c in group]
            for c in group:
                drop_lines.extend(c["lines"][1:])
            continue
        cue = group[0]
        if cue["per_line"] or len(cue["lines"]) == 1:
            for i in cue["translatable"]:
                line_map[str(i)] = add(lines[i])
        else:
            # A wrapped multi-line cue is ONE unit (joined with ␊), matching
            # the SRT pipeline; tail lines are dropped on write-back.
            line_map[str(cue["lines"][0])] = add("␊".join(lines[i] for i in cue["lines"]))
            drop_lines.extend(cue["lines"][1:])

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "line_map": line_map,
                                  "drop_lines": drop_lines, "groups": groups})
    app_logger.info(f"VTT: extracted {count} cue units")
    return json_path


def write_translated_content_to_vtt(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None,
                                    bilingual_mode=False):
    layout, original_data, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)

    # Original (joined) text per unit, for bilingual layout
    originals = {item["count_src"]: item["value"] for item in original_data}

    def bilingual(translated, original):
        if bilingual_mode and translated.strip() != original.strip():
            from core.engine.bilingual_format import style_markup
            return f"{style_markup(translated, 'vtt')}\n{original}"
        return translated

    lines = layout["lines"]
    drop = set(layout.get("drop_lines", []))
    for line_index, count in layout["line_map"].items():
        translated = translations.get(count)
        if not translated:
            continue
        original = originals.get(count, lines[int(line_index)])
        translated = drop_fabricated_tags(original, translated)
        lines[int(line_index)] = bilingual(
            translated.replace("␊", "\n").replace("␍", ""),
            original.replace("␊", "\n").replace("␍", ""))

    for count, members in layout.get("groups", {}).items():
        translated = translations.get(int(count))
        if not translated:
            continue
        source = originals[int(count)]
        pieces = split_across_cues(source, [flat(m["text"]) for m in members],
                                   drop_fabricated_tags(source, translated))
        for member, piece in zip(members, pieces):
            lines[member["line"]] = bilingual(piece, member["text"])

    # Remove tail lines that were merged into a grouped unit
    out_lines = [ln for idx, ln in enumerate(lines) if idx not in drop]

    result_path = subtitle_result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    app_logger.info(f"Translated VTT saved to: {result_path}")
    return result_path


# ------------------------------------------------------------------ ASS ----
_ASS_LEADING_TAGS = re.compile(r"^(?:\{[^}]*\})*")


def extract_ass_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    events = []
    in_events = False
    fields = ["layer", "start", "end", "style", "name", "marginl", "marginr",
              "marginv", "effect", "text"]   # default per spec
    text_field_index = 9

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

        plain = (_ASS_OVERRIDE.sub("", text)
                 .replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip())
        if not plain or not should_translate(plain):
            continue

        def field(name):
            k = fields.index(name) if name in fields else -1
            return parts[k].strip() if 0 <= k < text_field_index else ""

        lead = _ASS_LEADING_TAGS.match(text).group(0)
        try:
            start, end = seconds(field("start")), seconds(field("end"))
        except ValueError:
            start = end = None
        events.append({
            "line": i, "text": text, "plain": re.sub(r"\s+", " ", plain), "lead": lead,
            "prefix": head + ":" + ",".join(parts[:text_field_index]) + ",",
            "start": start, "end": end, "speaker": (field("style"), field("name")),
            # Inline override tags (karaoke {\k}, mid-line {\i1}) keep the line a
            # unit of its own; leading tags ({\an8}) are lifted off and re-applied
            # to that line, so a cut never lands inside a tag.
            "solo": start is None or "{" in text[len(lead):],
        })

    content_data = []
    count = 0
    cue_info = {}   # line index -> {count_src, prefix, tags}
    groups = {}     # count_src -> [{"line", "prefix", "lead", "text"}]

    def ass_info(e):
        return e["plain"], e["start"] or 0, e["end"] or 0, e["solo"], e["speaker"]

    for group in group_cues(events, ass_info):
        count += 1
        if len(group) > 1:
            content_data.append({"count_src": count, "type": "text",
                                 "value": join_texts([e["plain"] for e in group])})
            groups[str(count)] = [{"line": e["line"], "prefix": e["prefix"],
                                   "lead": e["lead"], "text": e["plain"]} for e in group]
            continue
        e = group[0]
        # Protect override tags {\...} with double-brace markers (covered by
        # the placeholder-survival check), and \N line breaks with ␊
        tags = _ASS_OVERRIDE.findall(e["text"])
        protected = e["text"]
        for tag_index, tag in enumerate(tags):
            protected = protected.replace(tag, f"{{{{ASS_{tag_index}}}}}", 1)
        # \N / \n = line break; \h = hard (non-breaking) space. Protect \h with
        # a sentinel so it round-trips instead of being sent literally to the
        # translator (which would mangle "word\hword").
        protected = protected.replace("\\N", "␊").replace("\\n", "␊").replace("\\h", "␠")
        content_data.append({"count_src": count, "type": "text", "value": protected})
        cue_info[str(e["line"])] = {"count_src": count, "prefix": e["prefix"], "tags": tags}

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "cue_info": cue_info, "groups": groups})
    app_logger.info(f"ASS: extracted {count} dialogue units")
    return json_path


def write_translated_content_to_ass(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    layout, original_data, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)
    originals = {item["count_src"]: item["value"] for item in original_data}

    lines = layout["lines"]
    for line_index, info in layout["cue_info"].items():
        translated = translations.get(info["count_src"])
        if not translated:
            continue
        translated = drop_fabricated_tags(originals.get(info["count_src"], ""), translated)
        text = translated.replace("␊", "\\N").replace("␠", "\\h").replace("␍", "")
        for tag_index, tag in enumerate(info["tags"]):
            text = text.replace(f"{{{{ASS_{tag_index}}}}}", tag)
        lines[int(line_index)] = info["prefix"] + text

    for count, members in layout.get("groups", {}).items():
        translated = translations.get(int(count))
        if not translated:
            continue
        source = originals[int(count)]
        pieces = split_across_cues(source, [m["text"] for m in members],
                                   drop_fabricated_tags(source, translated))
        for member, piece in zip(members, pieces):
            lines[member["line"]] = member["prefix"] + member["lead"] + piece

    result_path = subtitle_result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    app_logger.info(f"Translated ASS saved to: {result_path}")
    return result_path


# ------------------------------------------------------------------ LRC ----
_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _lrc_seconds(prefix):
    m, s, frac = _LRC_TAG.match(prefix).groups()
    return int(m) * 60 + int(s) + (float("0." + frac) if frac else 0.0)


def extract_lrc_content_to_json(file_path, temp_dir):
    content, _ = read_file_with_encoding(file_path)
    lines = content.splitlines()

    timed = []      # every timed line, translatable or not
    for i, line in enumerate(lines):
        match = _LRC_TIME_TAGS.match(line.strip())
        if match:   # metadata ([ti:...]) and plain lines stay untouched
            prefix, text = match.group(1), match.group(2).strip()
            timed.append({"line": i, "prefix": prefix, "text": text,
                          "start": _lrc_seconds(prefix)})
    # LRC has no end times: a line lasts until the next timed line (an empty
    # timed line marks an instrumental gap, which then shows up as a pause).
    for this, nxt in zip(timed, timed[1:] + [None]):
        this["end"] = nxt["start"] if nxt else this["start"]
    lyrics = [t for t in timed if t["text"] and should_translate(t["text"])]

    content_data = []
    count = 0
    line_info = {}  # line index -> {count_src, prefix}
    groups = {}     # count_src -> [{"line", "prefix", "text"}]

    def lrc_info(t):
        # A line with several timestamps is a repeated chorus: never merged.
        return t["text"], t["start"], t["end"], len(_LRC_TAG.findall(t["prefix"])) > 1, None

    for group in group_cues(lyrics, lrc_info):
        count += 1
        if len(group) > 1:
            content_data.append({"count_src": count, "type": "text",
                                 "value": join_texts([t["text"] for t in group])})
            groups[str(count)] = [{k: t[k] for k in ("line", "prefix", "text")} for t in group]
        else:
            content_data.append({"count_src": count, "type": "text", "value": group[0]["text"]})
            line_info[str(group[0]["line"])] = {"count_src": count, "prefix": group[0]["prefix"]}

    json_path = _save_extraction(file_path, temp_dir, content_data,
                                 {"lines": lines, "line_info": line_info, "groups": groups})
    app_logger.info(f"LRC: extracted {count} lyric units")
    return json_path


def write_translated_content_to_lrc(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    layout, original_data, translations = _load_for_writeback(
        file_path, temp_dir, original_json_path, translated_json_path)
    originals = {item["count_src"]: item["value"] for item in original_data}

    lines = layout["lines"]
    for line_index, info in layout["line_info"].items():
        translated = translations.get(info["count_src"])
        if translated:
            translated = drop_fabricated_tags(originals.get(info["count_src"], ""), translated)
            lines[int(line_index)] = info["prefix"] + translated.replace("␊", " ")

    for count, members in layout.get("groups", {}).items():
        translated = translations.get(int(count))
        if not translated:
            continue
        source = originals[int(count)]
        pieces = split_across_cues(source, [m["text"] for m in members],
                                   drop_fabricated_tags(source, translated))
        for member, piece in zip(members, pieces):
            lines[member["line"]] = member["prefix"] + piece

    result_path = subtitle_result_path(file_path, result_dir, src_lang, dst_lang)
    with open(result_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    app_logger.info(f"Translated LRC saved to: {result_path}")
    return result_path
