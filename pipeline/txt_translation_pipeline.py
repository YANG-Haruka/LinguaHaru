import json
import os
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_txt_content_to_json(file_path):
    """
    Extract all text content from TXT file and save in JSON format, each original paragraph counted separately
    Respect short lines as independent paragraphs, regardless of whether they end with punctuation
    """
    content_data = []  # For translation
    all_content_data = []  # Store all content with flags
    count = 0
    translate_count = 0
    
    # Read TXT file content
    with open(file_path, 'r', encoding='utf-8') as txt_file:
        content = txt_file.read()
        
    # Save original content
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    with open(os.path.join(temp_folder, "original_content.txt"), "w", encoding="utf-8") as original_file:
        original_file.write(content)
    
    # Split content by line
    lines = content.split('\n')
    
    # Process each line
    for line in lines:
        line = line.strip()
        
        # Process all non-empty lines
        if line:
            count += 1
            needs_translation = should_translate(line)
            
            line_data = {
                "count": count,
                "type": "paragraph",
                "value": line,
                "format": "\\x0a\\x0a",
                "needs_translation": needs_translation
            }
            
            all_content_data.append(line_data)
            
            # Add to translation queue if needed
            if needs_translation:
                translate_count += 1
                translate_item = {k: v for k, v in line_data.items() if k != "needs_translation"}
                content_data.append(translate_item)
    
    # Save translation queue
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    # Save all content with flags
    all_content_path = os.path.join(temp_folder, "all_content.json")
    with open(all_content_path, "w", encoding="utf-8") as all_file:
        json.dump(all_content_data, all_file, ensure_ascii=False, indent=4)
    
    app_logger.info(f"TXT content extracted to: {json_path}, {translate_count} translatable from {count} total paragraphs")
    return json_path

def write_translated_content_to_txt(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to a new TXT file, maintaining original paragraph format
    """
    # Load all content data
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    all_content_path = os.path.join(temp_folder, "all_content.json")
    
    with open(all_content_path, "r", encoding="utf-8") as all_file:
        all_content_data = json.load(all_file)
        
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    # Create translation map
    translation_map = {item["count"]: item["translated"] for item in translated_data}
    
    # Create output file
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{filename}_translated.txt")
    
    # Write content to new file
    with open(result_path, "w", encoding="utf-8") as result_file:
        for item in all_content_data:
            count = item["count"]
            needs_translation = item.get("needs_translation", True)
            
            # Use translation if available, otherwise use original text
            if needs_translation and count in translation_map:
                text_to_write = translation_map[count]
            else:
                text_to_write = item["value"]
            
            # Write text with paragraph separator
            result_file.write(text_to_write + "\n\n")
    
    app_logger.info(f"Translated TXT document saved to: {result_path}")
    return result_path