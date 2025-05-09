import json
import os
import re
from config.log_config import app_logger
from rich import box
from rich import markup
from rich.table import Table
from rich.console import Console
    

def detect_language_characters(text, lang_code):
    """
    Detect if text contains characters from specific language
    
    Args:
        text: Text to check
        lang_code: Language code to check for
        
    Returns:
        Boolean indicating if text contains characters of the specified language
    """
    patterns = {
        # East Asian languages
        "zh": r'[\u4e00-\u9fff]',  # Chinese (simplified)
        "zh-Hant": r'[\u4e00-\u9fff\u3400-\u4dbf]',  # Chinese (traditional)
        "ja": r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]',  # Japanese
        "ko": r'[\uac00-\ud7af\u1100-\u11ff]',  # Korean
        
        # Other non-Latin script languages
        "ru": r'[\u0400-\u04FF]',  # Russian (Cyrillic)
        "th": r'[\u0e00-\u0e7f]',  # Thai
        "vi": r'[\u00C0-\u1EF9]',  # Vietnamese (Latin with diacritics)
    }
    
    # For Latin-based languages, we simply check that the translation doesn't 
    # contain characters of the source language when it shouldn't
    latin_langs = ["en", "es", "fr", "de", "it", "pt"]
    
    if lang_code in patterns:
        pattern = re.compile(patterns[lang_code])
        status_lan = bool(pattern.search(text))
        return status_lan
    
    return False  # Default for Latin-based languages

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

def is_translation_valid(original, translated, src_lang, dst_lang):
    """
    Determine if a translation is valid based on language-specific rules
    
    Args:
        original: Original text
        translated: Translated text
        src_lang: Source language code
        dst_lang: Destination language code
        
    Returns:
        Boolean indicating if translation is valid
    """
    # Basic checks
    if not translated or translated.strip() == "":
        return False
 
     # Language-specific validation
    non_latin_langs = ["zh", "zh-Hant", "ja", "ko", "ru", "th"]
       
    # If translation is identical to original, it's likely not translated
    if translated.strip() == original.strip():
        if src_lang in non_latin_langs:
            if detect_language_characters(translated, src_lang):
                return False
            else:
                return True
        else:
            return False
    
    # Check if target language characters are present when they should be
    if dst_lang in non_latin_langs:
        # If target is non-Latin, translated text should contain target language characters
        if not detect_language_characters(translated, dst_lang):
            return False
    
    return True

def process_translation_results(original_text, translated_text, SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH, FAILED_JSON_PATH, src_lang, dst_lang, last_try=False):
    """
    Process translation results and save successful and failed translations.
    Updates translation status in the source split JSON file.
    """
    CONSOLE = Console(highlight=True, tab_size=4)
    
    if not translated_text:
        app_logger.warning("No translated text received.")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    successful_translations = []
    failed_translations = []
    result_dict = {}
    
    # Track successfully translated items
    successful_counts = []

    # Parse original JSON
    try:
        original_json = json.loads(clean_json(original_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse original JSON: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    # Parse translated JSON
    try:
        translated_json = json.loads(clean_json(translated_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse translated JSON: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    for key, value in original_json.items():
        # Get the translated value if it exists
        if translated_json is not None:
            translated_value = translated_json.get(key, "").strip()
        else:
            translated_value = ""
        
        # In last try mode, accept any non-empty translation as successful
        if last_try:
            if translated_value and translated_value.strip() != "":
                successful_translations.append({
                    "count": key,
                    "original": value,
                    "translated": translated_value
                })
                result_dict[key] = translated_value
                
                try:
                    successful_counts.append(int(key))
                except (ValueError, TypeError):
                    successful_counts.append(key)
            else:
                failed_translations.append({
                    "count": int(key), 
                    "value": value
                })
        else:
            # Normal mode: check if translation is valid
            if is_translation_valid(value, translated_value, src_lang, dst_lang):
                successful_translations.append({
                    "count": key,
                    "original": value,
                    "translated": translated_value
                })
                result_dict[key] = translated_value
                
                try:
                    successful_counts.append(int(key))
                except (ValueError, TypeError):
                    successful_counts.append(key)
            else:
                failed_translations.append({
                    "count": int(key), 
                    "value": value
                })

    # Create and display tables for successful and failed translations
    if successful_translations:
        # Create success table
        success_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Successful Translations",
            highlight=True,
            show_lines=True,
            border_style="green",
            collapse_padding=True,
        )
        success_table.add_column("Count", style="cyan", no_wrap=True)
        success_table.add_column("Original", style="white", overflow="fold")
        success_table.add_column("Translated", style="bright_green", overflow="fold")
        
        for item in successful_translations:
            success_table.add_row(
                str(item['count']),
                markup.escape(str(item['original'])),
                markup.escape(str(item['translated']))
            )
        
        # Display the table
        CONSOLE.print(success_table)
    
    # Create and display table for failed translations
    if failed_translations:
        border_style = "red" if last_try else "yellow"
        result_style = "bright_red" if last_try else "yellow"
        
        # Create failed table
        failed_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Failed Translations",
            highlight=True,
            show_lines=True,
            border_style=border_style,
            collapse_padding=True,
        )
        failed_table.add_column("Count", style="cyan", no_wrap=True)
        failed_table.add_column("Original", style="white", overflow="fold")
        failed_table.add_column("Result", style=result_style, overflow="fold")
        
        for item in failed_translations:
            if not translated_json.get(str(item['count']), "").strip():
                failed_table.add_row(
                    str(item['count']),
                    markup.escape(str(item['value'])),
                    markup.escape('""')
                )
            else:
                failed_table.add_row(
                    str(item['count']),
                    markup.escape(str(item['value'])),
                    markup.escape(str(translated_json.get(str(item['count']), '')))
                )
        
        # Display the failed table
        CONSOLE.print(failed_table)
 
    # Save successful translations
    save_json(RESULT_SPLIT_JSON_PATH, successful_translations)

    # Save failed translations
    if failed_translations:
        save_json(FAILED_JSON_PATH, failed_translations)
    
    # Update translation status in the source file
    if successful_counts:
        try:
            with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as f:
                src_data = json.load(f)
                
            # Update translation status
            updated_count = 0
            for item in src_data:
                count = item.get("count")
                # Ensure type matching by converting string count to integer
                if isinstance(count, str) and count.isdigit():
                    count = int(count)
                
                if count in successful_counts:
                    item["translated_status"] = True
                    updated_count += 1
                    
            # Save updated file
            with open(SRC_SPLIT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(src_data, f, ensure_ascii=False, indent=4)
                
        except Exception as e:
            # Create an error table
            error_table = Table(
                box=box.ASCII2,
                title="Error Updating Status",
                highlight=True,
                border_style="red",
                collapse_padding=True,
            )
            error_table.add_column("Error", style="bright_red")
            error_table.add_row(markup.escape(str(e)))
            
            # Display the error table
            CONSOLE.print(error_table)
            
            app_logger.error(f"Error updating translation status in source file: {e}")
    
    return result_dict

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