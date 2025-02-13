import json
import os
import re
from config.log_config import app_logger

def extract_srt_content_to_json(file_path):
    """
    Extract subtitles from an SRT file and save them in a JSON format.
    """
    with open(file_path, "r", encoding="utf-8") as file:
        srt_content = file.read()
    
    srt_pattern = re.compile(r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\Z)", re.DOTALL)
    
    content_data = []
    
    for match in srt_pattern.finditer(srt_content):
        count, start_time, end_time, value = match.groups()
        value = value.replace("\n", "␊").replace("\r", "␍")
        
        content_data.append({
            "count": int(count),
            "start_time": start_time,
            "end_time": end_time,
            "value": value
        })
    
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    return json_path

def write_translated_content_to_srt(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to the SRT file while keeping timestamps intact.
    """
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    translations = {item["count"]: item["translated"] for item in translated_data}
    
    output_srt_lines = []
    
    for item in original_data:
        count = item["count"]
        start_time = item["start_time"]
        end_time = item["end_time"]
        value = item["value"]
        translated_text = translations.get(str(count), value)
        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
        
        output_srt_lines.append(f"{count}\n{start_time} --> {end_time}\n{translated_text}\n\n")
    
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.srt")
    
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.writelines(output_srt_lines)
    
    app_logger.info(f"Translated SRT file saved to: {result_path}")
    return result_path
