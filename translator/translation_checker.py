import json
import os
import re
from config.log_config import app_logger

SRC_JSON_PATH = "temp/src.json"
RESULT_JSON_PATH = "temp/dst_translated.json"
FAILED_JSON_PATH = "temp/dst_translated_failed.json"

def clean_json(text):
    """Clean JSON text, remove markdown code blocks, handle BOM, and fix trailing commas."""
    if text is None:
        app_logger.warning("clean_json received None, returning empty string.")
        return ""
    if not isinstance(text, str):
        app_logger.warning(f"Expected string, but got {type(text)}. Converting to string.")
        text = str(text)

    text = text.strip().lstrip("\ufeff")  # Remove BOM if exists
    text = re.sub(r'^```json\n|\n```$', '', text, flags=re.MULTILINE)  # Remove Markdown JSON markers

    # Remove trailing commas inside JSON
    text = re.sub(r',\s*}', '}', text)  # Fix ", }" issue
    text = re.sub(r',\s*\]', ']', text)  # Fix ", ]" issue
    return text

def process_translation_results(original_text, translated_text):
    """Process translation results and save successful and failed translations"""
    if not translated_text:
        app_logger.warning("No translated text received.")
        _mark_all_as_failed(original_text)
        return

    successful_translations = []
    failed_translations = []

    # Parse original JSON
    try:
        original_json = json.loads(clean_json(original_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse original JSON: {e}")
        _mark_all_as_failed(original_text)
        return

    # Parse translated JSON
    try:
        translated_json = json.loads(clean_json(translated_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse translated JSON: {e}")
        _mark_all_as_failed(original_text)
        return

    for key, value in original_json.items():
        translated_value = translated_json.get(key, "").strip()
        if translated_value:
            successful_translations.append({
                "count": key,
                "original": value,
                "translated": translated_value
            })
        else:
            failed_translations.append({"count": int(key), "value": value})

    # Save successful translations
    save_json(RESULT_JSON_PATH, successful_translations)

    # Save failed translations
    if failed_translations:
        save_json(FAILED_JSON_PATH, failed_translations)
        app_logger.warning(f"Appended missing or empty keys to {FAILED_JSON_PATH}")

def _mark_all_as_failed(original_text):
    failed_segments = []

    try:
        original_json = json.loads(clean_json(original_text))
        for key, value in original_json.items():
            failed_segments.append({
                "count": int(key),
                "value": value.strip()
            })
    except json.JSONDecodeError as e:
        app_logger.warning(f"Error parsing original JSON during failure marking: {e}")
        return

    save_json(FAILED_JSON_PATH, failed_segments)
    app_logger.warning("All segments marked as failed due to translation errors.")

def save_json(filepath, data):
    """Save JSON data without overwriting existing content"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                existing_data = json.load(f)
                if not isinstance(existing_data, list):
                    existing_data = []
            except json.JSONDecodeError:
                existing_data = []
    else:
        existing_data = []

    existing_data.extend(data)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=4)

def check_and_sort_translations():
    """Check for missing translations and sort results."""
    missing_counts = set()

    if not os.path.exists(SRC_JSON_PATH) or not os.path.exists(RESULT_JSON_PATH):
        app_logger.error("Source or result file not found.")
        return missing_counts  # Return empty set

    with open(SRC_JSON_PATH, "r", encoding="utf-8") as src_file:
        try:
            src_data = json.load(src_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load source JSON.")
            return missing_counts

    with open(RESULT_JSON_PATH, "r", encoding="utf-8") as result_file:
        try:
            translated_data = json.load(result_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load translated JSON.")
            return missing_counts

    # Ensure src_data is in list format
    if isinstance(src_data, dict):
        src_data = [{"count": int(k), "original": v} for k, v in src_data.items()]

    translated_counts = {int(item["count"]) for item in translated_data}
    src_counts = {int(item["count"]) for item in src_data}
    missing_counts = src_counts - translated_counts

    if missing_counts:
        app_logger.warning(f"Missing translations for: {missing_counts}")
    else:
        app_logger.info("No missing counts detected. All segments are translated.")

    # Sort results by count
    sorted_data = sorted(translated_data, key=lambda x: int(x["count"]))

    with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=4)

    app_logger.info("Translation results have been sorted by count.")
    return missing_counts
