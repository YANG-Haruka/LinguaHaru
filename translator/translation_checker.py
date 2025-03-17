import json
import os
import re
from config.log_config import app_logger

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

def process_translation_results(original_text, translated_text, RESULT_SPLIT_JSON_PATH, FAILED_JSON_PATH):
    """Process translation results and save successful and failed translations"""
    if not translated_text:
        app_logger.warning("No translated text received.")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return False

    successful_translations = []
    failed_translations = []

    # Parse original JSON
    try:
        original_json = json.loads(clean_json(original_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse original JSON: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return False

    # Parse translated JSON
    try:
        translated_json = json.loads(clean_json(translated_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse translated JSON: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return False

    for key, value in original_json.items():
        # Get the translated value if it exists
        if translated_json is not None:
            translated_value = translated_json.get(key, "").strip()
        else:
            translated_value = ""
        
        # Check if we have a valid translation (not empty and not the same as the original)
        if translated_value and translated_value != value.strip():
            successful_translations.append({
                "count": key,
                "original": value,
                "translated": translated_value
            })
        else:
            failed_translations.append({"count": int(key), "value": value})

    # Use a fixed box width instead of calculating dynamically
    fixed_box_width = 100
    
    # Format successful translations within a text box
    if successful_translations:
        app_logger.info("+" + "-" * fixed_box_width + "+")
        for item in successful_translations:
            text = f"[{item['count']}] {item['original']} ==> {item['translated']}"
            padded_line = f"| {text}"
            app_logger.info(padded_line)
        app_logger.info("+" + "-" * fixed_box_width + "+")
    
    # Format failed translations within a text box if any exist
    if failed_translations:
        app_logger.warning("+" + "-" * fixed_box_width + "+")
        header = "FAILED TRANSLATIONS:"
        padded_header = f"| {header}"
        app_logger.warning(padded_header)
        app_logger.warning("+" + "-" * fixed_box_width + "+")
        
        for item in failed_translations:
            if not translated_json.get(str(item['count']), "").strip():
                text = f"[{item['count']}] {item['value']} ==> \"\""
            else:
                text = f"[{item['count']}] {item['value']} ==> {translated_json.get(str(item['count']), '')}"
            padded_line = f"| {text}"
            app_logger.warning(padded_line)
        app_logger.warning("+" + "-" * fixed_box_width + "+")

    # Save successful translations
    save_json(RESULT_SPLIT_JSON_PATH, successful_translations)

    # Save failed translations
    if failed_translations:
        save_json(FAILED_JSON_PATH, failed_translations)
        app_logger.info(f"Appended {len(failed_translations)} missing or invalid translations to {FAILED_JSON_PATH}")
        return True
    return False

def _mark_all_as_failed(original_text, FAILED_JSON_PATH):
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

def check_and_sort_translations(SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH):
    """
    Check for missing translations and sort results.
    If translations are missing, use the original text as translation result.
    """
    missing_counts = set()

    if not os.path.exists(SRC_SPLIT_JSON_PATH) or not os.path.exists(RESULT_SPLIT_JSON_PATH):
        app_logger.error("Source or result file not found.")
        return missing_counts  # Return empty set

    with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as src_file:
        try:
            src_data = json.load(src_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load source JSON.")
            return missing_counts

    with open(RESULT_SPLIT_JSON_PATH, "r", encoding="utf-8") as result_file:
        try:
            translated_data = json.load(result_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load translated JSON.")
            return missing_counts

    # Ensure src_data is in list format with proper structure
    src_data_list = []
    if isinstance(src_data, dict):
        for k, v in src_data.items():
            src_data_list.append({"count": int(k), "original": v})
    else:
        # Handle cases where src_data is already a list but might need restructuring
        for item in src_data:
            if isinstance(item, dict) and "count" in item:
                if "original" not in item and "value" in item:
                    item["original"] = item["value"]
                src_data_list.append(item)
    
    # Create a dictionary of translated items by count for quick lookup
    translated_dict = {int(item["count"]): item for item in translated_data}
    
    # Convert source counts to a set for comparison
    src_counts = {int(item["count"]) for item in src_data_list}
    
    # Find missing translations
    missing_counts = src_counts - set(translated_dict.keys())

    # If there are missing translations, add them using original text
    if missing_counts:
        app_logger.warning(f"Missing translations for: {missing_counts}")
        
        # Create a lookup dictionary for source items by count
        src_dict = {int(item["count"]): item for item in src_data_list}
        
        # Add missing translations using original text
        for count in missing_counts:
            if count in src_dict:
                original_text = src_dict[count].get("original", "")
                if not original_text and "value" in src_dict[count]:
                    original_text = src_dict[count]["value"]
                
                # Create a new entry with original text as translation
                new_entry = {
                    "count": count,
                    "original": original_text,
                    "translated": original_text  # Use original as translated
                }
                translated_data.append(new_entry)
    else:
        app_logger.info("No missing counts detected. All segments are translated.")

    # Sort results by count
    sorted_data = sorted(translated_data, key=lambda x: int(x["count"]))

    with open(RESULT_SPLIT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=4)

    app_logger.info("Translation results have been sorted by count.")
    return missing_counts