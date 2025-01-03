import json
import os
import re
from log_config import app_logger

SRC_JSON_PATH = "temp/src.json"
RESULT_JSON_PATH = "temp/dst_translated.json"
FAILED_JSON_PATH = "temp/dst_translated_failed.json"

def modify_json(entry):
    if not entry.startswith("```json\n"):
        entry = "```json\n" + entry
    if not entry.endswith("\n```"):
        entry = entry + "\n```"
    return entry

def clean_json(text):
    return re.sub(r'^```json\n|\n```$', '', text)

def process_translation_results(original_text, translated_text):
    translated_json = {}
    successful_translations = []
    failed_translations = []

    original_json = json.loads(clean_json(original_text))
    translated_lines = clean_json(translated_text).splitlines()
    
    if translated_lines[0].strip() == "{" and translated_lines[-1].strip() == "}":
        translated_lines = translated_lines[1:-1]

    for line in translated_lines:
        try:
            clean_line = line.strip().rstrip(",")
            line_json = json.loads("{" + clean_line + "}")
            translated_json.update(line_json)
        except json.JSONDecodeError as e:
            app_logger.warning(f"Translation error, content: {line}, error details: {e}")

    for key, value in original_json.items():
        if key not in translated_json or translated_json[key] == "":
            failed_translations.append({"count": int(key), "value": value})
        else:
            successful_translations.append({
                "count": key,
                "original": value,
                "translated": translated_json[key]
            })

    if os.path.exists(RESULT_JSON_PATH):
        with open(RESULT_JSON_PATH, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
    else:
        existing_data = []

    existing_data.extend(successful_translations)
    
    with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=4)
    app_logger.info(f"Successful translations saved to {RESULT_JSON_PATH}")

    if failed_translations:
        if os.path.exists(FAILED_JSON_PATH):
            with open(FAILED_JSON_PATH, "r", encoding="utf-8") as f:
                existing_failed_data = json.load(f)
        else:
            existing_failed_data = []
        
        existing_failed_data.extend(failed_translations)
        
        with open(FAILED_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(existing_failed_data, f, ensure_ascii=False, indent=4)
        app_logger.warning(f"Appended missing or empty keys to {FAILED_JSON_PATH}")

def check_and_sort_translations():
    if not os.path.exists(SRC_JSON_PATH) or not os.path.exists(RESULT_JSON_PATH):
        app_logger.error("Source or result file not found.")
        return
    
    with open(SRC_JSON_PATH, "r", encoding="utf-8") as src_file:
        src_data = json.load(src_file)

    with open(RESULT_JSON_PATH, "r", encoding="utf-8") as result_file:
        translated_data = json.load(result_file)
    
    translated_counts = {int(item["count"]) for item in translated_data}
    src_counts = {int(item["count"]) for item in src_data}
    missing_counts = src_counts - translated_counts

    if missing_counts:
        app_logger.warning("Missing counts detected:")
    else:
        app_logger.info("No missing counts detected. All segments are translated.")

    sorted_data = sorted(translated_data, key=lambda x: int(x["count"]))

    with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=4)
    
    app_logger.info("Translation results have been sorted by count.")
