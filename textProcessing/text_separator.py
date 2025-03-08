import json
import tiktoken
from tiktoken_ext import openai_public # For pyinstaller
import tiktoken_ext # For pyinstaller
import copy
import os
import re

def stream_segment_json(json_file_path, max_token, system_prompt, user_prompt, previous_prompt, previous_text):
    """
    Process JSON in segments, ensuring each segment's token count does not exceed max_token.
    Tracks and reports progress using count-based calculation.
    """
    # Load JSON data
    with open(json_file_path, "r", encoding="utf-8") as json_file:
        cell_data = json.load(json_file)

    if not cell_data:
        raise ValueError("cell_data is empty. Please check the input data.")

    max_count = max((cell.get("count", 0) for cell in cell_data), default=0)

    # Pre-calculate the initial token count from prompts and previous text
    prompt_token_count = sum(
        num_tokens_from_string(json.dumps(prompt, ensure_ascii=False))
        for prompt in [system_prompt, user_prompt, previous_prompt, previous_text]
    )

    # Iterator for JSON cells
    remaining_data = iter(cell_data)

    def get_next_segment():
        """
        Generator to yield JSON segments with token counts within the limit
        and progress updates.
        """
        nonlocal remaining_data

        current_segment_dict = {}
        current_token_count = prompt_token_count

        for cell in remaining_data:
            count = cell.get("count")
            value = cell.get("value", "").strip()
            if count is None or not value:
                continue  # Skip invalid or empty cells

            line_dict = {str(count): value}
            new_segment_str = f"```json\n{json.dumps(current_segment_dict | line_dict, ensure_ascii=False, indent=4)}\n```"
            new_token_count = prompt_token_count + num_tokens_from_string(new_segment_str)

            if new_token_count > max_token:
                # If adding this line exceeds the max_token, yield the current segment
                if current_segment_dict:
                    yield create_segment_output(current_segment_dict), calculate_progress(current_segment_dict, max_count)
                
                # Start a new segment with the current line
                current_segment_dict = line_dict
                current_token_count = prompt_token_count + num_tokens_from_string(
                    f"```json\n{json.dumps(current_segment_dict, ensure_ascii=False, indent=4)}\n```"
                )
            else:
                # Add the line to the current segment
                current_segment_dict.update(line_dict)
                current_token_count = new_token_count

        # Yield the final segment
        if current_segment_dict:
            yield create_segment_output(current_segment_dict), calculate_progress(current_segment_dict, max_count)

    return get_next_segment

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


def num_tokens_from_string(string):
    """
    Calculate the number of tokens in a text string.
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(string))

def split_text_by_token_limit(file_path, max_tokens=256):
    """
    Split long text items in JSON data into smaller chunks based on token limit
    while preserving complete sentences.
    
    Parameters:
    - file_path: Path to the JSON file to process
    - max_tokens: Maximum number of tokens allowed per chunk (default: 256)
    
    Returns:
    - Path to the saved split JSON file
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
                # Split the part itself by characters up to token limit
                encoding = tiktoken.get_encoding("cl100k_base")
                encoded_part = encoding.encode(part_with_punct)
                
                for j in range(0, len(encoded_part), max_tokens):
                    end_idx = min(j + max_tokens, len(encoded_part))
                    chunks.append(encoding.decode(encoded_part[j:end_idx]))
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
    
    Source format: Contains original_count, count and chunk fields
    Result format: {"count": "1", "original": "...", "translated": "..."}
    
    Parameters:
    - src_split_path: Path to source file
    - dst_translated_split_path: Path to translated file
    
    Returns:
    - Path to output file
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