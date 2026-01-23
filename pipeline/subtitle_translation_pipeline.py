import json
import os
import re
from config.log_config import app_logger

def extract_srt_content_to_json(file_path, temp_dir):
    """
    Extract subtitles from an SRT file and save them in a JSON format.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        srt_content = file.read()
    
    srt_pattern = re.compile(
        r"(\d+)\s*\r?\n"
        r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2},\d{3})\s*\r?\n"
        r"(.*?)(?=\r?\n\r?\n|\Z)",
        re.DOTALL
    )
    
    content_data = []
    
    for match in srt_pattern.finditer(srt_content):
        count, start_time, end_time, value = match.groups()
        value = value.replace("\n", "␊").replace("\r", "␍")
        
        content_data.append({
            "count_src": int(count),
            "start_time": start_time,
            "end_time": end_time,
            "value": value
        })
    
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    return json_path

def write_translated_content_to_srt(file_path, original_json_path, translated_json_path, result_dir, src_lang=None, dst_lang=None):
    """
    Write translated content back to the SRT file while keeping timestamps intact.

    Args:
        src_lang: Source language code (e.g., 'zh')
        dst_lang: Target language code (e.g., 'ja')
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
