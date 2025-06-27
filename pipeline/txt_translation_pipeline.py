import json
import os
import chardet
from .skip_pipeline import should_translate
from config.log_config import app_logger

def detect_file_encoding(file_path):
    """
    Detect file encoding using chardet library
    Returns the detected encoding or falls back to common encodings
    """
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding']
            confidence = result['confidence']
            
            app_logger.info(f"Detected encoding: {encoding} (confidence: {confidence:.2f})")
            
            # If confidence is too low, try common encodings
            if confidence < 0.7:
                app_logger.warning(f"Low confidence in detected encoding, will try fallback options")
                return None
            
            return encoding
    except Exception as e:
        app_logger.error(f"Error detecting encoding: {e}")
        return None

def read_file_with_encoding(file_path):
    """
    Read file content with automatic encoding detection
    Try multiple encoding strategies if needed
    """
    # First try to detect encoding
    detected_encoding = detect_file_encoding(file_path)
    
    # List of encodings to try (in order of preference)
    encodings_to_try = []
    
    if detected_encoding:
        encodings_to_try.append(detected_encoding)
    
    # Add common encodings as fallback
    fallback_encodings = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'utf-16le', 'utf-16be', 'latin1', 'cp1252']
    for enc in fallback_encodings:
        if enc not in encodings_to_try:
            encodings_to_try.append(enc)
    
    # Try each encoding until one works
    for encoding in encodings_to_try:
        try:
            app_logger.info(f"Trying to read file with encoding: {encoding}")
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            app_logger.info(f"Successfully read file with encoding: {encoding}")
            return content, encoding
        except UnicodeDecodeError as e:
            app_logger.warning(f"Failed to read with encoding {encoding}: {e}")
            continue
        except Exception as e:
            app_logger.error(f"Unexpected error with encoding {encoding}: {e}")
            continue
    
    # If all encodings fail, raise an exception
    raise Exception(f"Unable to decode file {file_path} with any supported encoding")

def extract_txt_content_to_json(file_path):
    """
    Extract all text content from TXT file and save in JSON format, each original paragraph counted separately
    Respect short lines as independent paragraphs, regardless of whether they end with punctuation
    Automatically detects file encoding to handle various text formats
    """
    content_data = []  # For translation
    all_content_data = []  # Store all content with flags
    count = 0
    translate_count = 0
    
    # Read TXT file content with automatic encoding detection
    try:
        content, used_encoding = read_file_with_encoding(file_path)
        app_logger.info(f"File read successfully using encoding: {used_encoding}")
    except Exception as e:
        app_logger.error(f"Failed to read file {file_path}: {e}")
        raise
        
    # Save original content
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    
    # Save original content (always in UTF-8)
    with open(os.path.join(temp_folder, "original_content.txt"), "w", encoding="utf-8") as original_file:
        original_file.write(content)
    
    # Save encoding info for reference
    encoding_info = {
        "original_encoding": used_encoding,
        "source_file": file_path,
        "processed_at": None  # Can be filled with timestamp if needed
    }
    with open(os.path.join(temp_folder, "encoding_info.json"), "w", encoding="utf-8") as encoding_file:
        json.dump(encoding_info, encoding_file, ensure_ascii=False, indent=4)
    
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
                "count_src": count,
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
    app_logger.info(f"Original encoding: {used_encoding}, converted to UTF-8")
    return json_path

def write_translated_content_to_txt(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to a new TXT file, maintaining original paragraph format
    Output file is always saved in UTF-8 encoding
    """
    # Load all content data
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    all_content_path = os.path.join(temp_folder, "all_content.json")
    
    try:
        with open(all_content_path, "r", encoding="utf-8") as all_file:
            all_content_data = json.load(all_file)
            
        with open(translated_json_path, "r", encoding="utf-8") as translated_file:
            translated_data = json.load(translated_file)
    except FileNotFoundError as e:
        app_logger.error(f"Required file not found: {e}")
        raise
    except json.JSONDecodeError as e:
        app_logger.error(f"Error parsing JSON file: {e}")
        raise
    
    # Create translation map
    translation_map = {item["count_src"]: item["translated"] for item in translated_data}
    
    # Create output file
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{filename}_translated.txt")
    
    # Write content to new file (always in UTF-8)
    try:
        with open(result_path, "w", encoding="utf-8") as result_file:
            for item in all_content_data:
                count = item["count_src"]
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
    
    except Exception as e:
        app_logger.error(f"Error writing translated content: {e}")
        raise