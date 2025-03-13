import json
import os
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_txt_content_to_json(file_path):
    """
    Extract all text content from TXT file and save in JSON format, each original paragraph counted separately
    Respect short lines as independent paragraphs, regardless of whether they end with punctuation
    """
    content_data = []
    count = 0
    
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
        
        # If line is not empty and should be translated
        if line and should_translate(line):
            count += 1
            content_data.append({
                "count": count,
                "type": "paragraph",
                "value": line,
                "format": "\\x0a\\x0a"
            })
    
    # Save content to JSON
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    app_logger.info(f"TXT content extracted to: {json_path}, total {count} paragraphs")
    return json_path

def write_translated_content_to_txt(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to a new TXT file, maintaining original paragraph format
    """
    # Load original JSON and translated JSON
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
        
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    # Create output file
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.txt")
    
    # Write translated content to new file
    with open(result_path, "w", encoding="utf-8") as result_file:
        for translated_item in translated_data:
            # Get translated text
            translated_text = translated_item["translated"]
            
            # Write translated text with paragraph separator
            result_file.write(translated_text + "\n\n")
    
    app_logger.info(f"Translated TXT document saved to: {result_path}")
    return result_path