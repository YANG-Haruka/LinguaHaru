import json
import tiktoken

def stream_segment_json(max_token, system_prompt, user_prompt, previous_prompt, previous_text):
    """
    Process JSON in segments, ensuring each segment's token count does not exceed max_token.
    Tracks and reports progress using count-based calculation.
    """
    try:
        # Load JSON data
        with open("temp/src.json", "r", encoding="utf-8") as json_file:
            cell_data = json.load(json_file)

        # Find the maximum count value
        max_count = max(cell.get("count", 0) for cell in cell_data)

        # Iterator for JSON cells
        remaining_data = iter(cell_data)

        def get_next_segment():
            """
            Generator to yield JSON segments with token counts within the limit
            and progress updates.
            """
            nonlocal remaining_data

            # Calculate initial token count from prompts and previous text
            previous_text_string = json.dumps(previous_text, ensure_ascii=False, indent=4)
            prompt_token_count = (
                num_tokens_from_string(json.dumps(system_prompt, ensure_ascii=False))
                + num_tokens_from_string(json.dumps(user_prompt, ensure_ascii=False))
                + num_tokens_from_string(json.dumps(previous_prompt, ensure_ascii=False))
                + num_tokens_from_string(previous_text_string)
            )

            current_segment_dict = {}
            current_token_count = prompt_token_count
            segment_last_count = 0

            for cell in remaining_data:
                count = cell.get("count")
                value = cell.get("value", "")
                if count is None or not value.strip():
                    continue

                # Simulate adding the current line to the segment
                line_dict = {str(count): value}
                temp_segment_dict = current_segment_dict.copy()
                temp_segment_dict.update(line_dict)
                temp_output_str = f"```json\n{json.dumps(temp_segment_dict, ensure_ascii=False, indent=4)}\n```"
                temp_token_count = prompt_token_count + num_tokens_from_string(temp_output_str)

                # Check if adding the current line exceeds max_token
                if temp_token_count > max_token:
                    # Yield current segment
                    output_str = f"```json\n{json.dumps(current_segment_dict, ensure_ascii=False, indent=4)}\n```"
                    segment_last_count = max(current_segment_dict.keys(), key=int)
                    progress = int(segment_last_count) / max_count if max_count > 0 else 1
                    print(f"Segment token count with prompt: {current_token_count}")
                    yield output_str, progress

                    # Start a new segment
                    current_segment_dict = line_dict
                    current_token_count = prompt_token_count + num_tokens_from_string(
                        f"```json\n{json.dumps(current_segment_dict, ensure_ascii=False, indent=4)}\n```"
                    )
                else:
                    # Add line to current segment
                    current_segment_dict.update(line_dict)
                    current_token_count = temp_token_count

            # Yield the final segment
            if current_segment_dict:
                output_str = f"```json\n{json.dumps(current_segment_dict, ensure_ascii=False, indent=4)}\n```"
                progress = int(segment_last_count) / max_count if max_count > 0 else 1
                yield output_str, progress

        return get_next_segment

    except Exception as e:
        print(f"Error in stream_segment_json: {e}")
        return None

def num_tokens_from_string(string):
    """
    Calculate the number of tokens in a text string.
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(string))
