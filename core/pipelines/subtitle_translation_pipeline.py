import json
import os
import re
from core.log_config import app_logger

def extract_srt_content_to_json(file_path, temp_dir):
    """
    Extract subtitles from an SRT file and save them in a JSON format.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        srt_content = file.read()
    
    # Tolerant of common SRT variants: 1-2 digit hours, '.' as millisecond
    # separator, 1-3 millisecond digits
    srt_pattern = re.compile(
        r"(\d+)\s*\r?\n"
        r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
        r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*\r?\n"
        r"(.*?)(?=\r?\n\r?\n|\Z)",
        re.DOTALL
    )

    content_data = []

    # Renumber sequentially: count_src is the translation lookup key, and
    # malformed files can repeat cue numbers, which would collapse entries
    for idx, match in enumerate(srt_pattern.finditer(srt_content), start=1):
        _, start_time, end_time, value = match.groups()
        value = value.replace("\n", "␊").replace("\r", "␍")

        content_data.append({
            "count_src": idx,
            "start_time": start_time.replace(".", ","),
            "end_time": end_time.replace(".", ","),
            "value": value
        })

    # Cues that don't match the pattern are silently absent from the output
    # file, so make the loss visible
    cue_count = srt_content.count("-->")
    if cue_count != len(content_data):
        app_logger.warning(
            f"SRT parse mismatch: file contains {cue_count} cues but only "
            f"{len(content_data)} were extracted; unparsed cues will be missing from the output"
        )
    
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

    for item in original_data:
        count = item["count_src"]
        start_time = item["start_time"]
        end_time = item["end_time"]
        value = item["value"]
        translated_text = translations.get(str(count), value)
        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")

        if bilingual_mode:
            original_text = value.replace("␊", "\n").replace("␍", "\r")
            if translated_text.strip() != original_text.strip():
                translated_text = f"{translated_text}\n{original_text}"

        output_srt_lines.append(f"{count}\n{start_time} --> {end_time}\n{translated_text}\n\n")

    result_folder = result_dir
    os.makedirs(result_folder, exist_ok=True)
    # Use source_lang2target_lang format if available, otherwise fallback to _translated
    if src_lang and dst_lang:
        lang_suffix = f"{src_lang}2{dst_lang}"
    else:
        lang_suffix = "translated"
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_{lang_suffix}.srt")
    
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.writelines(output_srt_lines)
    
    app_logger.info(f"Translated SRT file saved to: {result_path}")
    return result_path
