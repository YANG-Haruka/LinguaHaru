# /textProcessing/text_separator.py
import json
import copy
import os
import re
import shutil
import csv
from .calculation_tokens import num_tokens_from_string
from config.log_config import app_logger

def safe_convert_to_int(value):
    """安全地将各种格式的数字转换为整数"""
    if isinstance(value, int):
        return value
    
    if not isinstance(value, str):
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    
    # 移除空格
    value = value.strip()
    
    # 尝试直接转换
    try:
        return int(value)
    except ValueError:
        pass
    
    # 处理中文圆圈数字
    circle_nums = {
        '①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5,
        '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10,
        '⑪': 11, '⑫': 12, '⑬': 13, '⑭': 14, '⑮': 15,
        '⑯': 16, '⑰': 17, '⑱': 18, '⑲': 19, '⑳': 20,
        '㉑': 21, '㉒': 22, '㉓': 23, '㉔': 24, '㉕': 25,
        '㉖': 26, '㉗': 27, '㉘': 28, '㉙': 29, '㉚': 30,
        '㉛': 31, '㉜': 32, '㉝': 33, '㉞': 34, '㉟': 35,
        '㊱': 36, '㊲': 37, '㊳': 38, '㊴': 39, '㊵': 40,
        '㊶': 41, '㊷': 42, '㊸': 43, '㊹': 44, '㊺': 45,
        '㊻': 46, '㊼': 47, '㊽': 48, '㊾': 49, '㊿': 50
    }
    
    if value in circle_nums:
        return circle_nums[value]
    
    # 处理罗马数字
    roman_nums = {
        'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
        'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15,
        'XVI': 16, 'XVII': 17, 'XVIII': 18, 'XIX': 19, 'XX': 20
    }
    
    if value.upper() in roman_nums:
        return roman_nums[value.upper()]
    
    # 处理中文数字
    chinese_nums = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5,
        '陆': 6, '柒': 7, '捌': 8, '玖': 9, '拾': 10
    }
    
    if value in chinese_nums:
        return chinese_nums[value]
    
    # 尝试提取数字部分
    nums = re.findall(r'\d+', value)
    if nums:
        return int(nums[0])
    
    # 默认返回0
    return 0

def load_glossary(glossary_path, src_lang, dst_lang):
    """Load glossary from CSV file with multiple encodings"""
    encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin1', 'shift-jis', 'cp949']
    
    for encoding in encodings:
        try:
            with open(glossary_path, 'r', encoding=encoding) as csv_file:
                csv_reader = csv.reader(csv_file)
                
                # First row contains language codes
                lang_codes = next(csv_reader, None)
                if not lang_codes:
                    continue
                    
                # Find column indices
                src_idx = None
                dst_idx = None
                
                for i, code in enumerate(lang_codes):
                    if code.strip().lower() == src_lang.strip().lower():
                        src_idx = i
                    if code.strip().lower() == dst_lang.strip().lower():
                        dst_idx = i
                
                if src_idx is None or dst_idx is None:
                    continue
                
                # Read entries
                entries = []
                for row in csv_reader:
                    if len(row) > max(src_idx, dst_idx):
                        source_term = row[src_idx].strip()
                        target_term = row[dst_idx].strip()
                        
                        if source_term and target_term:
                            entries.append((source_term, target_term))
                
                if entries:
                    return entries
                
        except UnicodeDecodeError:
            continue
        except Exception as e:
            app_logger.warning(f"Error loading glossary with {encoding}: {e}")
            continue
    
    return []

def format_glossary_for_prompt(glossary_entries, text):
    """Format glossary entries for prompt"""
    relevant_entries = []
    for src_term, dst_term in glossary_entries:
        if src_term in text:
            relevant_entries.append((src_term, dst_term))
    
    if not relevant_entries:
        return ""
    
    glossary_lines = []
    for src_term, dst_term in relevant_entries:
        glossary_lines.append(f"{src_term} -> {dst_term}")
    
    formatted_glossary = "Glossary:\n" + "\n".join(glossary_lines)
    return formatted_glossary

def find_terms_with_hashtable(text, glossary_entries):
    """Find glossary terms in text using hash table"""
    term_dict = {src: dst for src, dst in glossary_entries}
    found_terms = set()
    results = []
    
    # Sort by length (longest first)
    sorted_terms = sorted(term_dict.keys(), key=len, reverse=True)
    
    for term in sorted_terms:
        if term in text and term not in found_terms:
            found_terms.add(term)
            results.append((term, term_dict[term]))
    
    return results

def stream_segment_json(json_file_path, max_token, system_prompt, user_prompt, previous_prompt, src_lang=None, dst_lang=None, glossary_path=None, continue_mode=False):
    """Process JSON in segments"""
    # Load glossary
    glossary_entries = []
    if src_lang and dst_lang and glossary_path and os.path.exists(glossary_path):
        glossary_entries = load_glossary(glossary_path, src_lang, dst_lang)
    
    # Handle file path
    if continue_mode and not os.path.exists(json_file_path):
        raise FileNotFoundError(f"Source file not found: {json_file_path}")
    
    # Create working copy
    file_dir = os.path.dirname(json_file_path)
    file_name = os.path.basename(json_file_path)
    base_name, ext = os.path.splitext(file_name)
    working_copy_path = os.path.join(file_dir, f"{base_name}_translating{ext}")
    
    if not os.path.exists(working_copy_path):
        if os.path.exists(json_file_path):
            shutil.copy2(json_file_path, working_copy_path)
        else:
            raise FileNotFoundError(f"Source file not found: {json_file_path}")
    
    # Load data
    with open(working_copy_path, "r", encoding="utf-8") as json_file:
        cell_data = json.load(json_file)

    if not cell_data:
        if os.path.exists(working_copy_path):
            os.remove(working_copy_path)
        raise ValueError("Empty data")

    # Calculate max count_split for progress
    max_count_split = max((safe_convert_to_int(cell.get("count_split", cell.get("count", 0))) for cell in cell_data), default=0)
    
    # Calculate token limits
    prompt_base_token_count = sum(
        num_tokens_from_string(json.dumps(prompt, ensure_ascii=False))
        for prompt in [system_prompt, user_prompt, previous_prompt]
        if prompt
    )
    
    segment_available_tokens = max_token - prompt_base_token_count
    
    if segment_available_tokens <= 0:
        segment_available_tokens = max(100, max_token // 2)
    
    # Pre-segment all data
    all_segments = []
    current_segment_dict = {}
    current_token_count = 0
    current_glossary_terms = []
    
    for i, cell in enumerate(cell_data):
        count_split = cell.get("count_split", cell.get("count"))
        value = cell.get("value", "").strip()
        
        # Skip translated content in continue mode
        if continue_mode and cell.get("translated_status", False):
            continue
            
        if count_split is None or not value:
            continue
        
        # Create line entry
        line_dict = {str(count_split): value}
        line_json = json.dumps(line_dict, ensure_ascii=False)
        line_tokens = num_tokens_from_string(line_json)
        
        # Find glossary terms
        segment_glossary_terms = []
        if glossary_entries:
            found_terms = find_terms_with_hashtable(value, glossary_entries)
            segment_glossary_terms = found_terms
        
        # Handle single line exceeding limit
        if line_tokens > segment_available_tokens:
            if current_segment_dict:
                progress = calculate_progress(current_segment_dict, max_count_split)
                segment_output = create_segment_output(current_segment_dict)
                all_segments.append((segment_output, progress, current_glossary_terms))
                
                current_segment_dict = {}
                current_token_count = 0
                current_glossary_terms = []
            
            # Split large text
            chunks = split_by_sentences_and_combine(value, segment_available_tokens)
            
            for chunk in chunks:
                chunk_dict = {str(count_split): chunk}
                chunk_json = json.dumps(chunk_dict, ensure_ascii=False)
                chunk_tokens = num_tokens_from_string(chunk_json)
                
                if chunk_tokens <= segment_available_tokens:
                    segment_dict = chunk_dict
                    progress = calculate_progress(segment_dict, max_count_split)
                    segment_output = create_segment_output(segment_dict)
                    all_segments.append((segment_output, progress, segment_glossary_terms))
        
        # Check if adding line exceeds limit
        elif current_token_count + line_tokens > segment_available_tokens:
            progress = calculate_progress(current_segment_dict, max_count_split)
            segment_output = create_segment_output(current_segment_dict)
            all_segments.append((segment_output, progress, current_glossary_terms))
            
            current_segment_dict = line_dict
            current_token_count = line_tokens
            current_glossary_terms = segment_glossary_terms
        else:
            current_segment_dict.update(line_dict)
            current_token_count += line_tokens
            current_glossary_terms.extend([term for term in segment_glossary_terms 
                                         if term not in current_glossary_terms])
    
    # Add last segment
    if current_segment_dict:
        progress = calculate_progress(current_segment_dict, max_count_split)
        segment_output = create_segment_output(current_segment_dict)
        all_segments.append((segment_output, progress, current_glossary_terms))
    
    # Clean up
    try:
        if os.path.exists(working_copy_path):
            os.remove(working_copy_path)
    except Exception as e:
        app_logger.warning(f"Warning: Could not remove working copy: {e}")
    
    return all_segments

def create_segment_output(segment_dict):
    """Create formatted JSON output"""
    return f"```json\n{json.dumps(segment_dict, ensure_ascii=False, indent=4)}\n```"

def calculate_progress(segment_dict, max_count_split):
    """Calculate progress percentage"""
    if not segment_dict or max_count_split <= 0:
        return 1.0
    
    try:
        last_count_split = max(safe_convert_to_int(key) for key in segment_dict.keys())
        return last_count_split / max_count_split
    except (ValueError, TypeError):
        return 1.0

def split_text_by_token_limit(file_path, max_tokens=256):
    """Split text by token limit with sequential count_split"""
    with open(file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    result = []
    next_count_split = 1  # Sequential counter
    
    for item_index, item in enumerate(json_data):
        # Process all items
        if not isinstance(item, dict):
            # Create minimal structure
            new_item = {
                "count_src": item_index + 1,
                "count_split": next_count_split,
                "value": str(item) if item is not None else "",
                "type": "text",
                "translated_status": False,
                "chunk": "1/1"
            }
            result.append(new_item)
            next_count_split += 1
            continue
            
        # Get count_src
        count_src = item.get("count_src", item_index + 1)
        
        # Get text value
        text = item.get("value", "")
        
        # Process all items
        tokens = num_tokens_from_string(text) if text else 0
        
        # Within limit or empty
        if tokens <= max_tokens:
            new_item = copy.deepcopy(item)
            new_item["count_src"] = count_src
            new_item["count_split"] = next_count_split
            new_item["translated_status"] = False
            new_item["chunk"] = "1/1"
            result.append(new_item)
            next_count_split += 1
        else:
            # Split long text
            try:
                chunks = split_by_sentences_and_combine(text, max_tokens)
                chunks_count = len(chunks) if chunks else 1
                
                # If no chunks, use original
                if not chunks:
                    chunks = [text]
                    chunks_count = 1
                
                for i, chunk_text in enumerate(chunks):
                    new_item = copy.deepcopy(item)
                    new_item["count_src"] = count_src
                    new_item["count_split"] = next_count_split
                    new_item["value"] = chunk_text
                    new_item["chunk"] = f"{i+1}/{chunks_count}"
                    new_item["translated_status"] = False
                    
                    result.append(new_item)
                    next_count_split += 1
                    
            except Exception as e:
                # Keep original on error
                app_logger.warning(f"Warning: Failed to split item {count_src}: {e}")
                new_item = copy.deepcopy(item)
                new_item["count_src"] = count_src
                new_item["count_split"] = next_count_split
                new_item["translated_status"] = False
                new_item["chunk"] = "1/1"
                result.append(new_item)
                next_count_split += 1
    
    app_logger.info(f"Split result: {len(json_data)} -> {len(result)} items")
    
    # Generate output path
    file_name = os.path.basename(file_path)
    file_base, file_ext = os.path.splitext(file_name)
    output_file_path = os.path.join(os.path.dirname(file_path), f"{file_base}_split{file_ext}")
    
    # Save
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    
    return output_file_path

def split_into_sentences(text):
    """Split text into sentences preserving formatting"""
    sentence_endings = [
        '。', '！', '？', '!', '?', '.', '；', ';'
    ]
    
    quote_brackets = [
        '"', '"', '"', "'", ''', ''', '）', ')', '）',
        '】', ']', '』', '》', '>'
    ]
    
    sentences = []
    current_sentence = ""
    
    i = 0
    while i < len(text):
        char = text[i]
        current_sentence += char
        
        if char in sentence_endings:
            # Look ahead for quotes
            j = i + 1
            while j < len(text) and text[j] in quote_brackets:
                current_sentence += text[j]
                j += 1
            
            # Preserve spaces
            while j < len(text) and text[j] == ' ':
                current_sentence += text[j]
                j += 1
            
            if current_sentence.strip():
                sentences.append(current_sentence)
            
            current_sentence = ""
            i = j - 1
        
        i += 1
    
    # Add remaining
    if current_sentence.strip():
        sentences.append(current_sentence)
    
    return sentences

def split_long_sentence(sentence, max_tokens):
    """Split long sentence by punctuation"""
    if num_tokens_from_string(sentence) <= max_tokens:
        return [sentence]
    
    internal_punctuation = [
        '，', ',', '；', ';', '：', ':', '、'
    ]
    
    trailing_marks = ['"', '"', '"', "'", ''', ''', '）', ')', '）', '】', ']', '』']
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    i = 0
    while i < len(sentence):
        char = sentence[i]
        current_chunk += char
        
        if char in internal_punctuation:
            # Look ahead for quotes
            j = i + 1
            while j < len(sentence) and sentence[j] in trailing_marks:
                current_chunk += sentence[j]
                j += 1
            
            # Preserve spaces
            while j < len(sentence) and sentence[j] == ' ':
                current_chunk += sentence[j]
                j += 1
            
            chunk_tokens = num_tokens_from_string(current_chunk)
            
            if current_tokens + chunk_tokens > max_tokens and current_chunk.strip():
                if current_chunk.strip():
                    chunks.append(current_chunk)
                current_chunk = ""
                current_tokens = 0
            else:
                current_tokens = chunk_tokens
            
            i = j - 1
        
        i += 1
    
    if current_chunk.strip():
        chunks.append(current_chunk)
    
    # If still too long, split by character count
    final_chunks = []
    for chunk in chunks:
        chunk_tokens = num_tokens_from_string(chunk)
        if chunk_tokens > max_tokens:
            chars_per_token = len(chunk) / chunk_tokens if chunk_tokens > 0 else 1
            chars_per_chunk = int(max_tokens * chars_per_token * 0.9)
            
            for start in range(0, len(chunk), chars_per_chunk):
                end = min(start + chars_per_chunk, len(chunk))
                final_chunks.append(chunk[start:end])
        else:
            final_chunks.append(chunk)
    
    return final_chunks

def split_by_sentences_and_combine(text, max_tokens):
    """Split text into sentences and combine up to token limit"""
    # Clean double punctuation
    cleaned_text = text
    punctuation_pairs = [
        ('。。', '。'), ('！！', '！'), ('？？', '？'),
        ('!!', '!'), ('??', '?'), ('..', '.'),
        ('，，', '，'), (',,', ',')
    ]
    
    for double, single in punctuation_pairs:
        cleaned_text = cleaned_text.replace(double, single)
    
    sentences = split_into_sentences(cleaned_text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    for sentence in sentences:
        sentence_tokens = num_tokens_from_string(sentence)
        
        # Single sentence exceeds limit
        if sentence_tokens > max_tokens:
            if current_chunk.strip():
                chunks.append(current_chunk)
                current_chunk = ""
                current_tokens = 0
            
            sentence_parts = split_long_sentence(sentence, max_tokens)
            chunks.extend(sentence_parts)
            continue
        
        # Adding would exceed limit
        if current_tokens + sentence_tokens > max_tokens and current_chunk.strip():
            chunks.append(current_chunk)
            current_chunk = sentence
            current_tokens = sentence_tokens
        else:
            current_chunk += sentence
            current_tokens += sentence_tokens
    
    if current_chunk.strip():
        chunks.append(current_chunk)
    
    return chunks

def deduplicate_translation_content(src_json_path):
    """Deduplicate content and maintain mapping"""
    with open(src_json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    # Track unique content
    content_to_deduped_count = {}  # content -> first count_deduped
    count_src_to_deduped_map = {}  # count_src -> count_deduped
    deduped_data = []
    next_count_deduped = 1
    
    app_logger.info(f"Deduplicating {len(json_data)} items")
    
    for item in json_data:
        if not isinstance(item, dict):
            continue
            
        count_src = item.get("count_src", item.get("count"))
        value = item.get("value", "")
        item_type = item.get("type", "text")
        
        if count_src is None:
            continue
        
        # Check if content exists
        if value in content_to_deduped_count:
            # Use existing count_deduped
            count_deduped = content_to_deduped_count[value]
        else:
            # New unique content
            count_deduped = next_count_deduped
            content_to_deduped_count[value] = count_deduped
            next_count_deduped += 1
            
            # Add to deduped data
            deduped_item = {
                "count_src": count_src,
                "count_deduped": count_deduped,
                "value": value,
                "type": item_type,
                "translated_status": False
            }
            deduped_data.append(deduped_item)
        
        # Record mapping
        count_src_to_deduped_map[count_src] = count_deduped
    
    # Stats
    total_items = len(json_data)
    unique_items = len(deduped_data)
    duplicate_count = total_items - unique_items
    
    app_logger.info(f"Deduplication: {total_items} -> {unique_items} items")
    if duplicate_count > 0:
        app_logger.info(f"Removed {duplicate_count} duplicates ({duplicate_count/total_items*100:.1f}% reduction)")
    
    return deduped_data, count_src_to_deduped_map

def create_deduped_json_for_translation(deduped_data, output_path):
    """Save deduplicated JSON"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(deduped_data, f, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Created deduped file: {output_path} ({len(deduped_data)} items)")
    return output_path

def restore_translations_from_deduped(dst_translated_split_path, count_src_to_deduped_map, src_original_path):
    """Restore translations to original structure
    """
    # Load translated results
    with open(dst_translated_split_path, 'r', encoding='utf-8') as f:
        translated_results = json.load(f)
    
    # Load the split file to get count_deduped -> count_split mapping
    split_file_path = dst_translated_split_path.replace('dst_translated_split.json', 'src_deduped_split.json')
    count_deduped_to_split_map = {}
    
    try:
        with open(split_file_path, 'r', encoding='utf-8') as f:
            split_data = json.load(f)
            
        # Build count_deduped -> count_split mapping
        for item in split_data:
            if not isinstance(item, dict):
                continue
            count_deduped = item.get("count_deduped")
            count_split = item.get("count_split")
            
            if count_deduped is not None and count_split is not None:
                if count_deduped not in count_deduped_to_split_map:
                    count_deduped_to_split_map[count_deduped] = []
                count_deduped_to_split_map[count_deduped].append(count_split)
        
        app_logger.info(f"Built count_deduped -> count_split mapping with {len(count_deduped_to_split_map)} entries")
        
    except Exception as e:
        app_logger.warning("Attempting direct mapping as fallback")
    
    # Build count_split -> translation mapping
    count_split_to_translation = {}
    
    for item in translated_results:
        if not isinstance(item, dict):
            continue
            
        count_split = item.get("count_split")
        translated_text = item.get("translated", "")
        
        if count_split is not None and translated_text:
            count_split_to_translation[count_split] = translated_text
    
    app_logger.info(f"Found {len(count_split_to_translation)} translations")
    
    # Load original data
    with open(src_original_path, 'r', encoding='utf-8') as f:
        original_data = json.load(f)
    
    # Restore translations
    result = []
    missing_translations = 0
    
    for item in original_data:
        if not isinstance(item, dict):
            continue
            
        count_src = item.get("count_src", item.get("count"))
        original_value = item.get("value", "")
        item_type = item.get("type", "text")
        
        if count_src is None:
            continue
        
        # Get translation through mapping chain: count_src -> count_deduped -> count_split -> translation
        translated_value = original_value  # Default to original
        
        if count_src in count_src_to_deduped_map:
            count_deduped = count_src_to_deduped_map[count_src]
            
            # Check if we have count_split mapping
            if count_deduped_to_split_map and count_deduped in count_deduped_to_split_map:
                # Get all count_splits for this count_deduped
                count_splits = count_deduped_to_split_map[count_deduped]
                
                # Combine translations from all splits
                translations = []
                for count_split in count_splits:
                    if count_split in count_split_to_translation:
                        translations.append(count_split_to_translation[count_split])
                
                if translations:
                    # Join all translations (in case text was split)
                    translated_value = "".join(translations)
                else:
                    missing_translations += 1
                    app_logger.warning(f"No translation found for count_deduped: {count_deduped} (count_splits: {count_splits})")
            else:
                # Fallback: try using count_deduped as count_split directly
                if count_deduped in count_split_to_translation:
                    translated_value = count_split_to_translation[count_deduped]
                else:
                    missing_translations += 1
                    app_logger.warning(f"No translation found for count_deduped: {count_deduped}")
        else:
            app_logger.warning(f"No deduped mapping found for count_src: {count_src}")
        
        # Create result entry
        result.append({
            "count_src": count_src,
            "type": item_type,
            "original": original_value,
            "translated": translated_value
        })
    
    # Statistics
    app_logger.info(f"Restoration complete: {len(result)} items, {missing_translations} missing translations")
    
    # Sort by count_src using safe conversion
    def get_count_key(item):
        count = item["count_src"]
        return safe_convert_to_int(count)
    
    result = sorted(result, key=get_count_key)
    
    # Save result
    dir_path = os.path.dirname(dst_translated_split_path)
    output_path = os.path.join(dir_path, "dst_translated.json")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    
    return output_path