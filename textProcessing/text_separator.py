import json
import copy
import os
import re
import shutil
import csv
import hashlib
from .calculation_tokens import num_tokens_from_string
from config.log_config import app_logger

def load_glossary(glossary_path, src_lang, dst_lang):
    """
    Load and process glossary from CSV file.
    Tries multiple common encodings to handle various file formats.
    """
    encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin1', 'shift-jis', 'cp949']
    
    for encoding in encodings:
        try:
            with open(glossary_path, 'r', encoding=encoding) as csv_file:
                csv_reader = csv.reader(csv_file)
                
                # First row contains language codes
                lang_codes = next(csv_reader, None)
                if not lang_codes:
                    continue
                    
                # Find column indices for source and target languages
                src_idx = None
                dst_idx = None
                
                for i, code in enumerate(lang_codes):
                    if code.strip().lower() == src_lang.strip().lower():
                        src_idx = i
                    if code.strip().lower() == dst_lang.strip().lower():
                        dst_idx = i
                
                # If we couldn't find matching language columns, try next encoding
                if src_idx is None or dst_idx is None:
                    # print(f"Warning: Could not find columns for {src_lang} and/or {dst_lang} in glossary with {encoding} encoding.")
                    continue
                
                # Read remaining rows as glossary entries
                entries = []
                for row in csv_reader:
                    if len(row) > max(src_idx, dst_idx):
                        source_term = row[src_idx].strip()
                        target_term = row[dst_idx].strip()
                        
                        # Only add if both terms are non-empty
                        if source_term and target_term:
                            entries.append((source_term, target_term))
                
                # If we successfully parsed entries, return them
                if entries:
                    return entries
                
        except UnicodeDecodeError:
            # Expected error when trying wrong encodings, continue silently
            continue
        except Exception as e:
            print(f"Error loading glossary with {encoding} encoding: {e}")
            continue
    
    # If we get here, all encodings failed
    # print(f"Failed to load glossary from {glossary_path} with any encoding.")
    return []

def format_glossary_for_prompt(glossary_entries, text):
    """
    Format glossary entries for inclusion in the prompt, filtering to only
    include terms that appear in the text.
    """
    # Filter glossary to only include terms that appear in the text
    relevant_entries = []
    for src_term, dst_term in glossary_entries:
        if src_term in text:
            relevant_entries.append((src_term, dst_term))
    
    if not relevant_entries:
        return ""
    
    # Format the glossary entries
    glossary_lines = []
    for src_term, dst_term in relevant_entries:
        glossary_lines.append(f"{src_term} -> {dst_term}")
    
    formatted_glossary = "Glossary:\n" + "\n".join(glossary_lines)
    return formatted_glossary

def find_terms_with_hashtable(text, glossary_entries):
    """
    Use a hash table approach for exact matching.
    Build a dictionary of source terms for O(1) lookups.
    """
    # Build lookup dictionary
    term_dict = {src: dst for src, dst in glossary_entries}
    
    # Use a set to track which terms we've already found
    found_terms = set()
    results = []
    
    # Sort terms by length (longest first) to prioritize longer matches
    sorted_terms = sorted(term_dict.keys(), key=len, reverse=True)
    
    for term in sorted_terms:
        if term in text and term not in found_terms:
            found_terms.add(term)
            results.append((term, term_dict[term]))
    
    return results

def stream_segment_json(json_file_path, max_token, system_prompt, user_prompt, previous_prompt, src_lang=None, dst_lang=None, glossary_path=None, continue_mode=False):
    """
    Process JSON in segments, pre-segmenting the content upfront and then returning all segments at once.
    In continue_mode, skip segments that are already translated.
    """
    # Load glossary if provided
    glossary_entries = []
    if src_lang and dst_lang and glossary_path and os.path.exists(glossary_path):
        glossary_entries = load_glossary(glossary_path, src_lang, dst_lang)
    
    # Create a working copy with "_translating" suffix
    file_dir = os.path.dirname(json_file_path)
    file_name = os.path.basename(json_file_path)
    base_name, ext = os.path.splitext(file_name)
    working_copy_path = os.path.join(file_dir, f"{base_name}_translating{ext}")
    
    # Copy the original file if working copy doesn't exist
    if not os.path.exists(working_copy_path):
        shutil.copy2(json_file_path, working_copy_path)
    
    # Load JSON data from working copy
    with open(working_copy_path, "r", encoding="utf-8") as json_file:
        cell_data = json.load(json_file)

    if not cell_data:
        # Clean up working copy if data is empty
        if os.path.exists(working_copy_path):
            os.remove(working_copy_path)
        raise ValueError("cell_data is empty. Please check the input data.")

    # Calculate maximum count value for progress calculation
    max_count = max((cell.get("count", 0) for cell in cell_data), default=0)
    
    # Calculate token count for prompts (excluding previous_text)
    prompt_base_token_count = sum(
        num_tokens_from_string(json.dumps(prompt, ensure_ascii=False))
        for prompt in [system_prompt, user_prompt, previous_prompt]
        if prompt  # Ignore None or empty strings
    )
    
    # Calculate segment token limit
    segment_available_tokens = max_token - prompt_base_token_count
    
    # Ensure there are enough tokens available
    if segment_available_tokens <= 0:
        print(f"Warning: No tokens available for content. Base prompts already use {prompt_base_token_count} tokens.")
        segment_available_tokens = max(100, max_token // 2)  # Set a minimum value
    
    # Pre-segment all the data
    all_segments = []
    current_segment_dict = {}
    current_token_count = 0
    current_processed_indices = []
    current_glossary_terms = []
    
    for i, cell in enumerate(cell_data):
        count = cell.get("count")
        value = cell.get("value", "").strip()
        if continue_mode and cell.get("translated_status", False):
            continue
            
        if count is None or not value:
            continue  # Skip invalid or empty cells
        
        # Create dictionary entry for current line
        line_dict = {str(count): value}
        line_json = json.dumps(line_dict, ensure_ascii=False)
        line_tokens = num_tokens_from_string(line_json)
        
        # Find relevant glossary terms for this text segment
        segment_glossary_terms = []
        if glossary_entries:
            found_terms = find_terms_with_hashtable(value, glossary_entries)
            segment_glossary_terms = found_terms
        
        # If a single line exceeds available tokens, split it into chunks
        if line_tokens > segment_available_tokens:
            # If we have a current segment, add it to all_segments before handling the long line
            if current_segment_dict:
                progress = calculate_progress(current_segment_dict, max_count)
                segment_output = create_segment_output(current_segment_dict)
                all_segments.append((segment_output, progress, current_glossary_terms))
                
                # Reset for next segment
                current_segment_dict = {}
                current_token_count = 0
                current_processed_indices = []
                current_glossary_terms = []
            
            # Split text into smaller chunks, ensuring complete sentences
            chunks = split_by_sentences_and_combine(value, segment_available_tokens)
            
            for chunk in chunks:
                chunk_dict = {str(count): chunk}
                chunk_json = json.dumps(chunk_dict, ensure_ascii=False)
                chunk_tokens = num_tokens_from_string(chunk_json)
                
                # Only add chunks that fit within the token limit
                if chunk_tokens <= segment_available_tokens:
                    segment_dict = chunk_dict
                    progress = calculate_progress(segment_dict, max_count)
                    segment_output = create_segment_output(segment_dict)
                    all_segments.append((segment_output, progress, segment_glossary_terms))
                else:
                    app_logger.warning(f"Warning: Chunk still too large ({chunk_tokens} tokens). Skipping this chunk.")
        
        # Check if adding this line would exceed the current segment's limit
        elif current_token_count + line_tokens > segment_available_tokens:
            # Current segment is full, add it to all_segments
            progress = calculate_progress(current_segment_dict, max_count)
            segment_output = create_segment_output(current_segment_dict)
            all_segments.append((segment_output, progress, current_glossary_terms))
            
            # Start a new segment with this line
            current_segment_dict = line_dict
            current_token_count = line_tokens
            current_processed_indices = [i]
            current_glossary_terms = segment_glossary_terms
        else:
            # Add the current line to the current segment
            current_segment_dict.update(line_dict)
            current_token_count += line_tokens
            current_processed_indices.append(i)
            current_glossary_terms.extend([term for term in segment_glossary_terms 
                                         if term not in current_glossary_terms])
    
    # Add the last segment if not empty
    if current_segment_dict:
        progress = calculate_progress(current_segment_dict, max_count)
        segment_output = create_segment_output(current_segment_dict)
        all_segments.append((segment_output, progress, current_glossary_terms))
    
    # Clean up the working copy file as we no longer need it
    try:
        if os.path.exists(working_copy_path):
            os.remove(working_copy_path)
    except Exception as e:
        print(f"Warning: Could not remove working copy file: {e}")
    
    # Return all segments at once
    return all_segments

def create_segment_output(segment_dict):
    """
    Create the formatted JSON segment output.
    """
    return f"```json\n{json.dumps(segment_dict, ensure_ascii=False, indent=4)}\n```"


def calculate_progress(segment_dict, max_count):
    """
    Calculate the progress percentage based on the last count in the segment.
    """
    if not segment_dict:
        return 1.0
    last_count = max(int(key) for key in segment_dict.keys())
    return last_count / max_count if max_count > 0 else 1.0

def split_text_by_token_limit(file_path, max_tokens=256):
    """
    Split long text items in JSON data into smaller chunks based on token limit
    while preserving complete sentences. Add translation status field.
    """
    # Load the original JSON file
    with open(file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    result = []
    
    for item in json_data:
        text = item["value"]
        tokens = num_tokens_from_string(text)
        
        # If under token limit, add as is with original_count field
        if tokens <= max_tokens:
            new_item = copy.deepcopy(item)
            new_item["original_count"] = item["count"]
            new_item["translated_status"] = False
            result.append(new_item)
            continue
        
        # For longer texts, split by complete sentences then recombine
        chunks = split_by_sentences_and_combine(text, max_tokens)
        chunks_count = len(chunks)
        
        for i, chunk_text in enumerate(chunks):
            new_item = copy.deepcopy(item)
            new_item["original_count"] = item["count"]
            new_item["count"] = len(result) + 1  # Assign a new sequential count
            new_item["value"] = chunk_text
            
            # Add chunk indicator for better tracking
            new_item["chunk"] = f"{i+1}/{chunks_count}"
            new_item["translated_status"] = False
            
            result.append(new_item)
    
    # Renumber the counts to ensure they're sequential
    for i, item in enumerate(result):
        item["count"] = i + 1
    
    # Generate the output file path
    file_name = os.path.basename(file_path)
    file_base, file_ext = os.path.splitext(file_name)
    output_file_path = os.path.join(os.path.dirname(file_path), f"{file_base}_split{file_ext}")
    
    # Save the split data
    with open(output_file_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    
    return output_file_path

def split_into_sentences(text):
    """
    Split text into sentences while preserving spacing between sentences.
    Handles Chinese, English, and other punctuation marks correctly.
    
    IMPORTANT: This function preserves ALL original spacing and formatting
    to ensure that when sentences are recombined, the result is identical 
    to the original text.
    """
    # Define sentence ending punctuation marks for multiple languages
    sentence_endings = [
        '。',  # Chinese period
        '！',  # Chinese exclamation
        '？',  # Chinese question mark
        '!',   # English exclamation
        '?',   # English question mark
        '.',   # English period
        '；',  # Chinese semicolon (sometimes used as sentence ending)
        ';'    # English semicolon (in some contexts)
    ]
    
    # Define quote marks and brackets that might follow sentence endings
    quote_brackets = [
        '"', '"', '"',  # Various double quotes
        "'", ''', ''',  # Various single quotes
        '）', ')', '）',  # Various closing parentheses
        '】', ']', '』', # Various closing brackets
        '》', '>',      # Angle brackets
    ]
    
    sentences = []
    current_sentence = ""
    
    i = 0
    while i < len(text):
        char = text[i]
        current_sentence += char
        
        # Check if current character is a sentence ending
        if char in sentence_endings:
            # Look ahead for any following quotes, brackets, or additional punctuation
            j = i + 1
            while j < len(text) and text[j] in quote_brackets:
                current_sentence += text[j]
                j += 1
            
            # CRITICAL: Preserve spaces after sentence endings to maintain original formatting
            while j < len(text) and text[j] == ' ':
                current_sentence += text[j]
                j += 1
            
            # Complete current sentence if it has content
            if current_sentence.strip():
                sentences.append(current_sentence)
            
            # Reset for next sentence
            current_sentence = ""
            i = j - 1  # Adjust index to account for consumed characters
        
        i += 1
    
    # Add any remaining content as the last sentence
    if current_sentence.strip():
        sentences.append(current_sentence)
    
    return sentences


def split_long_sentence(sentence, max_tokens):
    """
    Split an individual long sentence by internal punctuation marks.
    Updated to handle multiple languages and prevent double punctuation.
    
    PRESERVATION NOTE: This function attempts to maintain original spacing,
    but may modify it when splitting at internal punctuation points.
    """
    # If the sentence is within token limit, return as is
    if num_tokens_from_string(sentence) <= max_tokens:
        return [sentence]
    
    # Define internal punctuation patterns for multiple languages
    internal_punctuation = [
        '，',  # Chinese comma
        ',',   # English comma
        '；',  # Chinese semicolon
        ';',   # English semicolon
        '：',  # Chinese colon
        ':',   # English colon
        '、',  # Chinese enumeration comma
    ]
    
    # Quote marks and brackets that might follow internal punctuation
    trailing_marks = ['"', '"', '"', "'", ''', ''', '）', ')', '）', '】', ']', '』']
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    i = 0
    while i < len(sentence):
        char = sentence[i]
        current_chunk += char
        
        # Check if current character is internal punctuation
        if char in internal_punctuation:
            # Look ahead for any following quotes or brackets
            j = i + 1
            while j < len(sentence) and sentence[j] in trailing_marks:
                current_chunk += sentence[j]
                j += 1
            
            # PRESERVE spaces after internal punctuation
            while j < len(sentence) and sentence[j] == ' ':
                current_chunk += sentence[j]
                j += 1
            
            # Calculate tokens for current chunk
            chunk_tokens = num_tokens_from_string(current_chunk)
            
            # If adding this chunk would exceed limit, save current chunk and start new one
            if current_tokens + chunk_tokens > max_tokens and current_chunk.strip():
                if current_chunk.strip():
                    chunks.append(current_chunk)  # Keep original formatting
                current_chunk = ""
                current_tokens = 0
            else:
                current_tokens = chunk_tokens
            
            i = j - 1  # Adjust index
        
        i += 1
    
    # Add remaining chunk if it has content
    if current_chunk.strip():
        chunks.append(current_chunk)
    
    # If we still have chunks that are too long, split by character count
    # WARNING: This may break text preservation guarantees
    final_chunks = []
    for chunk in chunks:
        chunk_tokens = num_tokens_from_string(chunk)
        if chunk_tokens > max_tokens:
            # Estimate characters per token for this chunk
            chars_per_token = len(chunk) / chunk_tokens if chunk_tokens > 0 else 1
            chars_per_chunk = int(max_tokens * chars_per_token * 0.9)  # Leave some margin
            
            # Split by character count - this may break word boundaries
            for start in range(0, len(chunk), chars_per_chunk):
                end = min(start + chars_per_chunk, len(chunk))
                final_chunks.append(chunk[start:end])
        else:
            final_chunks.append(chunk)
    
    return final_chunks


def split_by_sentences_and_combine(text, max_tokens):
    """
    Split text into sentences, then combine sentences up to the token limit.
    Updated to prevent double punctuation marks while preserving original format.
    
    PRESERVATION GUARANTEE: When chunks are rejoined with ''.join(), 
    the result should be identical to the original text.
    """
    # Clean any existing double punctuation marks from input
    cleaned_text = text
    punctuation_pairs = [
        ('。。', '。'),  # Double Chinese periods
        ('！！', '！'),  # Double Chinese exclamations
        ('？？', '？'),  # Double Chinese question marks
        ('!!', '!'),    # Double English exclamations
        ('??', '?'),    # Double English question marks
        ('..', '.'),    # Double English periods (but be careful with ellipsis ...)
        ('，，', '，'),  # Double Chinese commas
        (',,', ','),    # Double English commas
    ]
    
    for double, single in punctuation_pairs:
        cleaned_text = cleaned_text.replace(double, single)
    
    # Split into complete sentences using improved function
    sentences = split_into_sentences(cleaned_text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    for sentence in sentences:
        sentence_tokens = num_tokens_from_string(sentence)
        
        # If a single sentence exceeds the limit, split it further
        if sentence_tokens > max_tokens:
            # First add any accumulated chunk
            if current_chunk.strip():
                chunks.append(current_chunk)  # Preserve exact formatting
                current_chunk = ""
                current_tokens = 0
            
            # Split the long sentence and add its parts
            sentence_parts = split_long_sentence(sentence, max_tokens)
            chunks.extend(sentence_parts)
            continue
        
        # If adding this sentence would exceed the limit, start a new chunk
        if current_tokens + sentence_tokens > max_tokens and current_chunk.strip():
            chunks.append(current_chunk)  # Preserve exact formatting
            current_chunk = sentence
            current_tokens = sentence_tokens
        else:
            # Add to current chunk - sentences already include proper spacing
            current_chunk += sentence
            current_tokens += sentence_tokens
    
    # Add the last chunk if not empty
    if current_chunk.strip():
        chunks.append(current_chunk)  # Preserve exact formatting
    
    return chunks

def recombine_split_jsons(src_split_path, dst_translated_split_path):
    """
    Merge source file and translated file based on original_count from source.
    Combine multiple chunks with the same count into one complete content.
    """    
    try:
        with open(src_split_path, 'r', encoding='utf-8') as f:
            src_data = json.load(f)
    except Exception as e:
        print(f"Error loading source file: {e}")
        src_data = []
    
    try:
        with open(dst_translated_split_path, 'r', encoding='utf-8') as f:
            translated_data = json.load(f)
    except Exception as e:
        print(f"Error loading translated file: {e}")
        translated_data = []
    
    # Organize translation data by count
    translated_by_count = {}
    for item in translated_data:
        if not isinstance(item, dict) or "count" not in item:
            continue
        
        count = str(item["count"])
        
        if count not in translated_by_count:
            translated_by_count[count] = {
                "original": item.get("original", ""),
                "translated": item.get("translated", "")
            }
        else:
            # Concatenate translation content for multiple chunks
            translated_by_count[count]["original"] += item.get("original", "")
            translated_by_count[count]["translated"] += item.get("translated", "")
    
    # Group by original_count and get complete original text
    result_by_original_count = {}
    
    # CRITICAL PRESERVATION STEP: Get original complete text instead of reconstructing from chunks
    original_texts = {}  # original_count -> complete original text
    
    # Find corresponding src_deduped.json from src_split_path directory
    src_dir = os.path.dirname(src_split_path)
    src_deduped_path = os.path.join(src_dir, "src_deduped.json")
    
    if os.path.exists(src_deduped_path):
        try:
            with open(src_deduped_path, 'r', encoding='utf-8') as f:
                deduped_data = json.load(f)
            
            for item in deduped_data:
                count = str(item.get("count", ""))
                value = item.get("value", "")
                if count and value:
                    original_texts[count] = value
                    
        except Exception as e:
            print(f"Warning: Could not load original texts from {src_deduped_path}: {e}")
    else:
        print(f"Warning: Original deduped file not found at {src_deduped_path}")
    
    # Process each source data item
    for item in src_data:
        count = str(item.get("count", ""))
        original_count = str(item.get("original_count", count))
        
        if not count:
            continue
        
        # Use original complete text to guarantee preservation
        if original_count in original_texts:
            original_text = original_texts[original_count]
        else:
            # Fallback: use current item's value (may not preserve formatting perfectly)
            original_text = item.get("value", "")
            print(f"Warning: Using fallback text for original_count {original_count}")
        
        # Get corresponding translation
        translated_text = ""
        if count in translated_by_count:
            translated_text = translated_by_count[count]["translated"]
        
        # Add to results
        if original_count not in result_by_original_count:
            result_by_original_count[original_count] = {
                "count": int(original_count) if original_count.isdigit() else original_count,
                "type": item.get("type", "text"),
                "original": original_text,
                "translated": translated_text
            }
        else:
            # If already exists, append translation content only
            # (original text should be identical, so don't modify it)
            existing_translated = result_by_original_count[original_count]["translated"]
            result_by_original_count[original_count]["translated"] = existing_translated + translated_text
    
    # Convert to list and sort
    result = list(result_by_original_count.values())
    
    def get_count_key(item):
        count = item["count"]
        if isinstance(count, int) or (isinstance(count, str) and count.isdigit()):
            return int(count)
        return count
    
    result = sorted(result, key=get_count_key)
    
    # Generate output path
    dir_path = os.path.dirname(dst_translated_split_path)
    base_name = os.path.basename(dst_translated_split_path)
    file_name = base_name.replace("_split", "")
    output_path = os.path.join(dir_path, file_name)
    
    # Save result
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    
    return output_path

def deduplicate_translation_content(src_json_path):
    """
    Deduplicates content in the source JSON file before translation.
    Returns unique contents and mapping from content hash to counts.
    """
    with open(src_json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    # Maps from content hash to the actual content
    unique_contents = {}
    # Maps from content hash to a list of count values where this content appears
    hash_to_counts_map = {}
    
    for item in json_data:
        count = item.get("count")
        value = item.get("value", "").strip()
        
        if not value:
            continue
            
        # Generate a hash for the content
        content_hash = hashlib.md5(value.encode('utf-8')).hexdigest()
        
        # If this is the first time we see this content, add it to unique_contents
        if content_hash not in unique_contents:
            unique_contents[content_hash] = value
            hash_to_counts_map[content_hash] = []
            
        # Record that this content hash was found at this count
        hash_to_counts_map[content_hash].append(count)
    
    app_logger.info(f"Reduced {len(json_data)} items to {len(unique_contents)} unique content items")
    return unique_contents, hash_to_counts_map

def create_deduped_json_for_translation(unique_contents, output_path):
    """
    Creates a JSON file with only unique content for translation.
    """
    deduped_data = []
    
    for i, (content_hash, value) in enumerate(unique_contents.items(), 1):
        deduped_data.append({
            "count": i,
            "value": value,
            "original_hash": content_hash,
            "translated_status": False
        })
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(deduped_data, f, ensure_ascii=False, indent=4)
    
    return output_path

def restore_translations_to_original_structure(deduped_translated_path, hash_to_counts_map, original_json_path, output_path):
    """
    Restores translations from the deduplicated format back to the original structure.
    """
    # Load the deduplicated translations
    with open(deduped_translated_path, 'r', encoding='utf-8') as f:
        deduped_translations = json.load(f)
    
    # Create a mapping from content hash to translated content
    hash_to_translation = {}
    
    # Check the format of deduped_translations to determine how to extract translations
    if deduped_translations and isinstance(deduped_translations, list):
        # If from recombine_split_jsons output format
        if all(isinstance(item, dict) and "original" in item and "translated" in item for item in deduped_translations):
            app_logger.info("Using recombined format for deduped translations")
            # Create a hash mapping from original content to translated content
            for item in deduped_translations:
                original_text = item.get("original", "").strip()
                translated_text = item.get("translated", "")
                
                if original_text and translated_text:
                    content_hash = hashlib.md5(original_text.encode('utf-8')).hexdigest()
                    hash_to_translation[content_hash] = translated_text
        # If directly from translation results containing original_hash
        elif all(isinstance(item, dict) and "original_hash" in item for item in deduped_translations):
            app_logger.info("Using direct format for deduped translations")
            for item in deduped_translations:
                original_hash = item.get("original_hash")
                translated = item.get("translated", "")
                if original_hash and translated:
                    hash_to_translation[original_hash] = translated
    
    if not hash_to_translation:
        app_logger.error("Failed to create hash to translation mapping. No translations to restore.")
        return deduped_translated_path
    
    # Load the original JSON to get the full structure
    with open(original_json_path, 'r', encoding='utf-8') as f:
        original_data = json.load(f)
    
    # Create the restored translations
    restored_data = []
    
    for item in original_data:
        count = item.get("count")
        original = item.get("value", "").strip()
        if not original:
            continue
            
        content_hash = hashlib.md5(original.encode('utf-8')).hexdigest()
        translated = hash_to_translation.get(content_hash, "")
        
        restored_data.append({
            "count": count,
            "original": original,
            "translated": translated
        })
    
    # Save the restored translations
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(restored_data, f, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Restored translations to original structure: {len(restored_data)} items")
    return output_path