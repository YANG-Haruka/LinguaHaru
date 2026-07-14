import json
import os
import chardet
from .skip_pipeline import should_translate
from core.log_config import app_logger

def read_file_with_encoding(file_path):
    """
    Read file content with automatic encoding detection
    Try multiple encoding strategies if needed
    """
    # chardet's BEST guess goes first, even at low confidence. Decoding validates
    # it (a wrong codec raises UnicodeDecodeError and we move on), so trying the
    # guess first is strictly safer than discarding it and falling straight to the
    # greedy gbk — which "successfully" decodes euc-kr/shift_jis bytes into WRONG
    # CJK characters (silent mojibake). This is why a Korean/Japanese subtitle whose
    # confidence hovers around the 0.7 line no longer gets mangled as Chinese.
    encodings_to_try = []
    try:
        with open(file_path, 'rb') as f:
            guess = chardet.detect(f.read())
        if guess.get('encoding'):
            app_logger.info(f"Detected encoding: {guess['encoding']} "
                            f"(confidence: {guess.get('confidence') or 0:.2f})")
            encodings_to_try.append(guess['encoding'])
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"Error detecting encoding: {e}")

    # Add common encodings as fallback. CJK multi-byte codecs (gbk/big5/shift_jis/
    # euc-*) come before the latin1/cp1252 catch-alls, which decode ANY byte and so
    # must stay last (reaching them means real detection failed).
    fallback_encodings = ['utf-8', 'gbk', 'gb2312', 'big5', 'shift_jis', 'euc-kr',
                          'euc-jp', 'utf-16', 'utf-16le', 'utf-16be', 'latin1', 'cp1252']
    for enc in fallback_encodings:
        if enc not in encodings_to_try:
            encodings_to_try.append(enc)
    
    # Try each encoding until one works
    for encoding in encodings_to_try:
        try:
            app_logger.info(f"Trying to read file with encoding: {encoding}")
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            if encoding in ('latin1', 'cp1252'):
                # latin1 decodes any byte sequence, so reaching it usually means
                # the real encoding was not recognized
                app_logger.warning(f"File decoded with fallback encoding {encoding}; "
                                   f"if the source is CJK text the result may be garbled")
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

def detect_newline(file_path):
    """Detect the dominant line ending of a text file from its raw bytes.

    Python's text-mode reading collapses all newlines to '\\n', so the original
    style must be sniffed from bytes. Returns '\\r\\n', '\\r' or '\\n'.
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except Exception:
        return "\n"
    if b"\r\n" in data:
        return "\r\n"
    if b"\r" in data:
        return "\r"
    return "\n"


def extract_txt_content_to_json(file_path, temp_dir):
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
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    
    # Save original content (always in UTF-8)
    with open(os.path.join(temp_folder, "original_content.txt"), "w", encoding="utf-8") as original_file:
        original_file.write(content)
    
    # Save encoding info for reference
    encoding_info = {
        "original_encoding": used_encoding,
        "source_file": file_path,
        "newline": detect_newline(file_path),  # preserve CRLF/CR/LF on write-back
        "processed_at": None  # Can be filled with timestamp if needed
    }
    with open(os.path.join(temp_folder, "encoding_info.json"), "w", encoding="utf-8") as encoding_file:
        json.dump(encoding_info, encoding_file, ensure_ascii=False, indent=4)
    
    # Split content by line, keeping every line (including blank ones) so the
    # original line structure can be reproduced on write-back
    lines = content.split('\n')

    for line in lines:
        line = line.rstrip('\r')
        stripped = line.strip()
        count += 1
        needs_translation = bool(stripped) and should_translate(stripped)

        line_data = {
            "count_src": count,
            "type": "paragraph",
            "value": stripped,
            "raw": line,
            "needs_translation": needs_translation
        }

        all_content_data.append(line_data)

        # Add to translation queue if needed
        if needs_translation:
            translate_count += 1
            translate_item = {k: v for k, v in line_data.items() if k not in ("needs_translation", "raw")}
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

def write_translated_content_to_txt(file_path, original_json_path, translated_json_path, temp_dir, result_dir, src_lang=None, dst_lang=None, bilingual_mode=False):
    """
    Write translated content back to a new TXT file, maintaining original paragraph format
    Output file is always saved in UTF-8 encoding

    Args:
        src_lang: Source language code (e.g., 'zh')
        dst_lang: Target language code (e.g., 'ja')
        bilingual_mode: If True, each translated line is followed by its
                        original line (untranslated/blank lines stay single)
    """
    # Load all content data
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    all_content_path = os.path.join(temp_folder, "all_content.json")

    # Recover the original line ending so CRLF/CR files round-trip unchanged
    newline = "\n"
    try:
        with open(os.path.join(temp_folder, "encoding_info.json"), "r", encoding="utf-8") as ef:
            newline = json.load(ef).get("newline", "\n") or "\n"
    except Exception:
        pass

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
    result_folder = result_dir
    os.makedirs(result_folder, exist_ok=True)
    # Use source_lang2target_lang format if available, otherwise fallback to _translated
    if src_lang and dst_lang:
        lang_suffix = f"{src_lang}2{dst_lang}"
    else:
        lang_suffix = "translated"
    result_path = os.path.join(result_folder, f"{filename}_{lang_suffix}.txt")
    
    # Write content to new file (always in UTF-8), reproducing the original
    # line structure: blank lines, single newlines and indentation are kept
    try:
        # newline="" disables Python's platform newline translation so the
        # detected line ending is written verbatim and deterministically
        with open(result_path, "w", encoding="utf-8", newline="") as result_file:
            output_lines = []
            for item in all_content_data:
                count = item["count_src"]
                needs_translation = item.get("needs_translation", True)
                raw = item.get("raw", item["value"])

                if needs_translation and count in translation_map:
                    leading_ws = raw[:len(raw) - len(raw.lstrip())]
                    translated_line = leading_ws + translation_map[count]
                    output_lines.append(translated_line)
                    if bilingual_mode and translated_line.strip() != raw.strip():
                        # Bilingual: translated line followed by the original line
                        output_lines.append(raw)
                else:
                    output_lines.append(raw)
            result_file.write(newline.join(output_lines))
        
        app_logger.info(f"Translated TXT document saved to: {result_path}")
        return result_path
    
    except Exception as e:
        app_logger.error(f"Error writing translated content: {e}")
        raise