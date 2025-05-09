import json
import copy
import os
import re
import shutil
import csv
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
                    print(f"Warning: Could not find columns for {src_lang} and/or {dst_lang} in glossary with {encoding} encoding.")
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
    print(f"Failed to load glossary from {glossary_path} with any encoding.")
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

def stream_segment_json(json_file_path, max_token, system_prompt, user_prompt, previous_prompt, src_lang=None, dst_lang=None, glossary_path=None):
    """
    Process JSON in segments, pre-segmenting the content upfront and then returning all segments at once.
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
    
    # Pre-calculate token count for prompts (excluding previous_text)
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
    while preserving complete sentences.
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
    Split text into complete sentences, ensuring sentence endings stay with their content.
    Works with both Chinese and English sentence endings.
    """
    # Pattern for Chinese and English sentence endings (。!?！？) followed by optional quotes, brackets, etc.
    sentence_end_pattern = r'([。！？!?]["""\'）\)）]*)'
    
    # Split text by sentence endings, keeping the endings
    parts = re.split(f'({sentence_end_pattern})', text)
    
    # Combine each part with its sentence ending
    sentences = []
    i = 0
    current_sentence = ""
    
    while i < len(parts):
        current_sentence += parts[i]
        
        # If next part is a sentence ending, add it to current sentence and finish this sentence
        if i + 1 < len(parts) and re.match(sentence_end_pattern, parts[i + 1]):
            current_sentence += parts[i + 1]
            sentences.append(current_sentence)
            current_sentence = ""
            i += 2
        else:
            i += 1
    
    # Add any remaining text as a separate sentence (might not end with punctuation)
    if current_sentence.strip():
        sentences.append(current_sentence)
    
    # Filter out empty sentences
    return [s for s in sentences if s.strip()]

def split_long_sentence(sentence, max_tokens):
    """
    Split an individual long sentence by commas or other internal punctuation
    if it exceeds the token limit.
    """
    # If the sentence is within limit, return it as is
    if num_tokens_from_string(sentence) <= max_tokens:
        return [sentence]
    
    # Internal punctuation pattern (commas, semicolons, colons in both Chinese and English)
    internal_punct_pattern = r'([，,；;：:]["""\'）\)）]*)'
    
    # Split the sentence by internal punctuation
    parts = re.split(f'({internal_punct_pattern})', sentence)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    i = 0
    while i < len(parts):
        part = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) and re.match(internal_punct_pattern, parts[i + 1]) else ""
        
        part_with_punct = part + punct
        part_tokens = num_tokens_from_string(part_with_punct)
        
        # If adding this part would exceed the limit
        if current_tokens + part_tokens > max_tokens:
            # If current chunk is not empty, add it to chunks
            if current_chunk:
                chunks.append(current_chunk)
            
            # If this single part exceeds the limit, we need to split it by characters
            if part_tokens > max_tokens:
                chars_per_token = len(part_with_punct) / part_tokens
                for j in range(0, len(part_with_punct), int(max_tokens * chars_per_token)):
                    end_idx = min(j + int(max_tokens * chars_per_token), len(part_with_punct))
                    chunks.append(part_with_punct[j:end_idx])
            else:
                # Otherwise, start a new chunk with this part
                current_chunk = part_with_punct
                current_tokens = part_tokens
        else:
            # Add to the current chunk
            current_chunk += part_with_punct
            current_tokens += part_tokens
        
        i += 2 if punct else 1
    
    # Add the last chunk if not empty
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

def split_by_sentences_and_combine(text, max_tokens):
    """
    Split text into sentences, then combine sentences up to the token limit.
    If a single sentence exceeds the limit, split it at internal punctuation.
    """
    # First, split into complete sentences
    sentences = split_into_sentences(text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    for sentence in sentences:
        sentence_tokens = num_tokens_from_string(sentence)
        
        # If a single sentence exceeds the limit, we need to split it
        if sentence_tokens > max_tokens:
            # First add any accumulated chunk
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
                current_tokens = 0
            
            # Then split the long sentence and add its parts
            sentence_parts = split_long_sentence(sentence, max_tokens)
            chunks.extend(sentence_parts)
            continue
        
        # If adding this sentence would exceed the limit, start a new chunk
        if current_tokens + sentence_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            current_chunk = sentence
            current_tokens = sentence_tokens
        else:
            # Add to current chunk
            current_chunk += sentence
            current_tokens += sentence_tokens
    
    # Add the last chunk if not empty
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

def recombine_split_jsons(src_split_path, dst_translated_split_path):
    """
    Merge source file and translated file based on original_count from source.
    Combine multiple chunks with the same count into one complete content.
    """    
    # Load source and translation files
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
    
    # Create mapping from count to original_count
    count_to_original_count = {}
    # Organize content by count
    count_chunks = {}
    
    # 1. Collect all chunks from source file, organize by count
    for item in src_data:
        count = str(item.get("count", ""))
        if not count:
            continue
            
        # Get original count
        original_count = str(item.get("original_count", count))
        count_to_original_count[count] = original_count
        
        # Get chunk info
        chunk_info = item.get("chunk", "1/1")
        try:
            chunk_num, total_chunks = map(int, chunk_info.split('/'))
        except:
            chunk_num, total_chunks = 1, 1
        
        # Get content
        content = item.get("value", "")
        if not content:
            continue
        
        # Initialize or update entry for this count
        if count not in count_chunks:
            count_chunks[count] = {
                "chunks": [None] * total_chunks,
                "type": item.get("type", "text"),
                "original_count": original_count
            }
        elif len(count_chunks[count]["chunks"]) < total_chunks:
            # Extend chunks list if needed
            count_chunks[count]["chunks"].extend([None] * (total_chunks - len(count_chunks[count]["chunks"])))
        
        # Set content at the right position
        if 0 <= chunk_num-1 < len(count_chunks[count]["chunks"]):
            count_chunks[count]["chunks"][chunk_num-1] = content
    
    # 2. Process translations, organize by count
    translated_by_count = {}
    for item in translated_data:
        if not isinstance(item, dict) or "count" not in item:
            continue
        
        count = str(item["count"])
        
        # Initialize if first time seeing this count
        if count not in translated_by_count:
            translated_by_count[count] = {
                "original": item.get("original", ""),
                "translated": item.get("translated", "")
            }
        else:
            # If count exists, append translation content
            translated_by_count[count]["original"] += item.get("original", "")
            translated_by_count[count]["translated"] += item.get("translated", "")
    
    # 3. Merge results using original_count as the final count
    result_by_original_count = {}
    
    for count, data in count_chunks.items():
        original_count = data["original_count"]
        original_text = "".join([chunk for chunk in data["chunks"] if chunk])
        
        # Get corresponding translation
        translated_text = original_text  # Default to original text
        if count in translated_by_count:
            translated_text = translated_by_count[count]["translated"]
        
        # If this original_count doesn't exist yet, add it
        if original_count not in result_by_original_count:
            result_by_original_count[original_count] = {
                "count": int(original_count) if original_count.isdigit() else original_count,
                "type": data["type"],
                "original": original_text,
                "translated": translated_text
            }
        else:
            # If exists, append content
            result_by_original_count[original_count]["original"] += original_text
            result_by_original_count[original_count]["translated"] += translated_text
    
    # Convert to list
    result = list(result_by_original_count.values())
    
    # Sort by count
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