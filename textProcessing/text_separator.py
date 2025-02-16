import json
import tiktoken
from tiktoken_ext import openai_public # For pyinstaller
import tiktoken_ext # For pyinstaller

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
