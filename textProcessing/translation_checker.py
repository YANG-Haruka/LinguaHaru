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
    """
    patterns = {
        # East Asian
        "zh": r'[\u4e00-\u9fff]',  # Chinese simplified
        "zh-Hant": r'[\u4e00-\u9fff\u3400-\u4dbf]',  # Chinese traditional
        "ja": r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]',  # Japanese
        "ko": r'[\uac00-\ud7af\u1100-\u11ff]',  # Korean
        
        # Other scripts
        "ru": r'[\u0400-\u04FF]',  # Russian
        "th": r'[\u0e00-\u0e7f]',  # Thai
        "vi": r'[\u00C0-\u1EF9]',  # Vietnamese
    }
    
    # Latin languages
    latin_langs = ["en", "es", "fr", "de", "it", "pt"]
    
    if lang_code in patterns:
        pattern = re.compile(patterns[lang_code])
        status_lan = bool(pattern.search(text))
        return status_lan
    
    return False

def clean_json(text):
    """Clean JSON text"""
    if text is None:
        app_logger.warning("clean_json received None")
        return ""
    if not isinstance(text, str):
        app_logger.warning(f"Expected string, got {type(text)}")
        text = str(text)

    text = text.strip().lstrip("\ufeff")  # Remove BOM
    text = re.sub(r'^```json\n|\n```$', '', text, flags=re.MULTILINE)  # Remove markdown

    # Fix trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*\]', ']', text)
    return text

def is_translation_valid(original, translated, src_lang, dst_lang):
    """
    Check if translation is valid
    """
    # Basic checks
    if not translated or translated.strip() == "":
        return False
 
    # Language validation
    non_latin_langs = ["zh", "zh-Hant", "ja", "ko", "ru", "th"]
       
    # Check if identical
    if translated.strip() == original.strip():
        if src_lang in non_latin_langs:
            if detect_language_characters(translated, src_lang):
                return False
            else:
                return True
        else:
            return False
    
    # Check target language
    if dst_lang in non_latin_langs:
        if not detect_language_characters(translated, dst_lang):
            return False
    
    return True

def process_translation_results(original_text, translated_text, SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH, FAILED_JSON_PATH, src_lang, dst_lang, last_try=False):
    """
    Process translation results
    """
    CONSOLE = Console(highlight=True, tab_size=4)
    
    if not translated_text:
        app_logger.warning("No translated text received")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    successful_translations = []
    failed_translations = []
    result_dict = {}
    
    # Track successful count_splits
    successful_count_splits = []

    # Parse original
    try:
        original_json = json.loads(clean_json(original_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse original: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    # Parse translated
    try:
        translated_json = json.loads(clean_json(translated_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse translated: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    # Check if all identical (not last try)
    if not last_try:
        if translated_json == original_json:
            # Check if first try
            existing_fail = []
            try:
                with open(FAILED_JSON_PATH, 'r', encoding='utf-8') as f:
                    for item in json.load(f):
                        existing_fail.append(item.get('count_split'))
            except Exception:
                pass
            
            orig_counts = []
            for k in original_json.keys():
                try:
                    orig_counts.append(int(k))
                except:
                    orig_counts.append(k)
            is_first_try = not set(orig_counts).issubset(set(existing_fail))
            
            if is_first_try:
                app_logger.info("First attempt - displaying results")
                fail_table = Table(
                    box=box.ASCII2,
                    expand=True,
                    title="Failed Translations (First Attempt)",
                    highlight=True,
                    show_lines=True,
                    border_style="yellow",
                    collapse_padding=True,
                )
                fail_table.add_column("Split Count", style="cyan", no_wrap=True)
                fail_table.add_column("Original", style="white", overflow="fold")
                fail_table.add_column("Translated", style="yellow", overflow="fold")
                for key, value in original_json.items():
                    fail_table.add_row(str(key), markup.escape(str(value)), markup.escape(str(value)))
                Console(highlight=True, tab_size=4).print(fail_table)
                
                _mark_all_as_failed(original_text, FAILED_JSON_PATH)
                return { k: v for k, v in original_json.items() }
            else:
                app_logger.warning("All translations identical - marking as failed")
                fail_table = Table(
                    box=box.ASCII2,
                    expand=True,
                    title="Failed Translations",
                    highlight=True,
                    show_lines=True,
                    border_style="yellow",
                    collapse_padding=True,
                )
                fail_table.add_column("Split Count", style="cyan", no_wrap=True)
                fail_table.add_column("Original", style="white", overflow="fold")
                fail_table.add_column("Translated", style="yellow", overflow="fold")
                for key, value in original_json.items():
                    fail_table.add_row(str(key), markup.escape(str(value)), markup.escape(str(value)))
                Console(highlight=True, tab_size=4).print(fail_table)
                _mark_all_as_failed(original_text, FAILED_JSON_PATH)
                return {}

    # Process each item
    for key, value in original_json.items():
        # Get translated value
        if translated_json is not None:
            translated_value = translated_json.get(key, "").strip()
        else:
            translated_value = ""
        
        # Last try mode - accept any non-empty
        if last_try:
            if translated_value and translated_value.strip() != "":
                successful_translations.append({
                    "count_split": int(key),
                    "original": value,
                    "translated": translated_value
                })
                result_dict[key] = translated_value
                
                try:
                    successful_count_splits.append(int(key))
                except (ValueError, TypeError):
                    successful_count_splits.append(key)
            else:
                failed_translations.append({
                    "count_split": int(key), 
                    "value": value
                })
        else:
            # Normal mode - validate translation
            if is_translation_valid(value, translated_value, src_lang, dst_lang):
                successful_translations.append({
                    "count_split": int(key),
                    "original": value,
                    "translated": translated_value
                })
                result_dict[key] = translated_value
                
                try:
                    successful_count_splits.append(int(key))
                except (ValueError, TypeError):
                    successful_count_splits.append(key)
            else:
                failed_translations.append({
                    "count_split": int(key), 
                    "value": value
                })

    # Display successful translations
    if successful_translations:
        success_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Successful Translations",
            highlight=True,
            show_lines=True,
            border_style="green",
            collapse_padding=True,
        )
        success_table.add_column("Split Count", style="cyan", no_wrap=True)
        success_table.add_column("Original", style="white", overflow="fold")
        success_table.add_column("Translated", style="bright_green", overflow="fold")
        
        for item in successful_translations:
            success_table.add_row(
                str(item['count_split']),
                markup.escape(str(item['original'])),
                markup.escape(str(item['translated']))
            )
        
        CONSOLE.print(success_table)
    
    # Display failed translations
    if failed_translations:
        border_style = "red" if last_try else "yellow"
        result_style = "bright_red" if last_try else "yellow"
        
        failed_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Failed Translations",
            highlight=True,
            show_lines=True,
            border_style=border_style,
            collapse_padding=True,
        )
        failed_table.add_column("Split Count", style="cyan", no_wrap=True)
        failed_table.add_column("Original", style="white", overflow="fold")
        failed_table.add_column("Result", style=result_style, overflow="fold")
        
        for item in failed_translations:
            if not translated_json.get(str(item['count_split']), "").strip():
                failed_table.add_row(
                    str(item['count_split']),
                    markup.escape(str(item['value'])),
                    markup.escape('""')
                )
            else:
                failed_table.add_row(
                    str(item['count_split']),
                    markup.escape(str(item['value'])),
                    markup.escape(str(translated_json.get(str(item['count_split']), '')))
                )
        
        CONSOLE.print(failed_table)
 
    # Save successful translations
    save_json(RESULT_SPLIT_JSON_PATH, successful_translations)

    # Save failed translations
    if failed_translations:
        save_failed_json_without_duplicates(FAILED_JSON_PATH, failed_translations)
    
    # Update translation status in source file
    if successful_count_splits:
        try:
            with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as f:
                src_data = json.load(f)
                
            # Update status
            updated_count = 0
            for item in src_data:
                # Use correct field name: count_split
                count_split = item.get("count_split")
                
                # Ensure type matching
                if count_split is not None:
                    try:
                        count_split_int = int(count_split)
                        if count_split_int in successful_count_splits:
                            item["translated_status"] = True
                            updated_count += 1
                    except (ValueError, TypeError):
                        app_logger.warning(f"Invalid count_split: {count_split}")
                
            # Save updated file
            with open(SRC_SPLIT_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(src_data, f, ensure_ascii=False, indent=4)
                
            app_logger.info(f"Updated translation status for {updated_count} items")
                
        except Exception as e:
            # Error table
            error_table = Table(
                box=box.ASCII2,
                title="Error Updating Status",
                highlight=True,
                border_style="red",
                collapse_padding=True,
            )
            error_table.add_column("Error", style="bright_red")
            error_table.add_row(markup.escape(str(e)))
            
            CONSOLE.print(error_table)
            app_logger.error(f"Error updating translation status: {e}")
    
    return result_dict

def _mark_all_as_failed(original_text, FAILED_JSON_PATH):
    """Mark all segments as failed"""
    failed_segments = []

    try:
        original_json = json.loads(clean_json(original_text))
        for key, value in original_json.items():
            failed_segments.append({
                "count_split": int(key),
                "value": value.strip()
            })
    except json.JSONDecodeError as e:
        app_logger.warning(f"Error parsing original: {e}")
        return

    save_failed_json_without_duplicates(FAILED_JSON_PATH, failed_segments)
    app_logger.warning("All segments marked as failed")

def save_json(filepath, data):
    """Save JSON data"""
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

def save_failed_json_without_duplicates(filepath, data):
    """Save failed JSON without duplicates"""
    # Load existing
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

    # Track existing count_splits
    existing_count_splits = set()
    for item in existing_data:
        count_split = item.get("count_split")
        if count_split is not None:
            try:
                existing_count_splits.add(int(count_split))
            except (ValueError, TypeError):
                existing_count_splits.add(count_split)

    # Add new items
    for item in data:
        count_split = item.get("count_split")
        if count_split is not None:
            try:
                count_split_int = int(count_split)
            except (ValueError, TypeError):
                count_split_int = count_split
            
            if count_split_int not in existing_count_splits:
                existing_data.append(item)
                existing_count_splits.add(count_split_int)

    # Save
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=4)

def check_and_sort_translations(SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH):
    """
    Check missing translations and sort results
    """
    missing_count_splits = set()

    if not os.path.exists(SRC_SPLIT_JSON_PATH) or not os.path.exists(RESULT_SPLIT_JSON_PATH):
        app_logger.error("Source or result file not found")
        return missing_count_splits

    with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as src_file:
        try:
            src_data = json.load(src_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load source JSON")
            return missing_count_splits

    with open(RESULT_SPLIT_JSON_PATH, "r", encoding="utf-8") as result_file:
        try:
            translated_data = json.load(result_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load translated JSON")
            return missing_count_splits

    # Get all source count_splits
    src_count_splits = set()
    src_dict = {}
    
    for item in src_data:
        if isinstance(item, dict):
            count_split = item.get("count_split")
            if count_split is not None:
                try:
                    count_split_int = int(count_split)
                    src_count_splits.add(count_split_int)
                    src_dict[count_split_int] = item
                except (ValueError, TypeError):
                    pass
    
    # Get translated count_splits
    translated_dict = {}
    for item in translated_data:
        if isinstance(item, dict):
            count_split = item.get("count_split")
            if count_split is not None:
                try:
                    count_split_int = int(count_split)
                    translated_dict[count_split_int] = item
                except (ValueError, TypeError):
                    pass
    
    # Find missing
    missing_count_splits = src_count_splits - set(translated_dict.keys())

    # Add missing translations
    if missing_count_splits:
        app_logger.warning(f"Missing translations for count_splits: {missing_count_splits}")
        
        for count_split in missing_count_splits:
            if count_split in src_dict:
                original_text = src_dict[count_split].get("value", "")
                
                # Create new entry
                new_entry = {
                    "count_split": count_split,
                    "original": original_text,
                    "translated": original_text  # Use original as translated
                }
                translated_data.append(new_entry)
    else:
        app_logger.info("No missing translations")

    # Sort by count_split
    sorted_data = sorted(translated_data, key=lambda x: int(x.get("count_split", 0)))

    with open(RESULT_SPLIT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=4)

    app_logger.info("Translation results sorted")
    return missing_count_splits