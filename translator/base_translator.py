import os
import shutil
from llmWrapper.ollama_wrapper import translate_text
from textProcessing.text_separator import stream_segment_json
from prompts.load_prompt import load_prompt
import json
import re

def modify_json(entry):
    if not entry.startswith("```json\n"):
        entry = "```json\n" + entry
    if not entry.endswith("\n```"):
        entry = entry + "\n```"
    return entry

def clean_json(text):
    """delete ```json\n and \n``` """
    return re.sub(r'^```json\n|\n```$', '', text)

def compare_translation(original_text, translated_text):
    failed_json_path = "temp/dst_translated_failed.json"
    missing_or_empty = {}
    try:
        original_json = json.loads(clean_json(original_text))
        translated_json = json.loads(clean_json(translated_text))

        for key, value in original_json.items():
            if key not in translated_json or translated_json[key] == "":
                missing_or_empty[key] = value

        if missing_or_empty:
            os.makedirs("temp", exist_ok=True)
            if os.path.exists(failed_json_path):
                with open(failed_json_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            else:
                existing_data = []
            if missing_or_empty:
                existing_data.append(missing_or_empty)
            if existing_data:
                with open(failed_json_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=4)
                print(f"Appended missing or empty keys to {failed_json_path}")

    except (json.JSONDecodeError, ValueError) as e:
        print(f"compare_translation error: {e}")
        raise

class DocumentTranslator:
    def __init__(self, input_file_path, model, src_lang, dst_lang, max_token, previous_text=None):
        self.input_file_path = input_file_path
        self.model = model
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.max_token = max_token
        self.previous_text = previous_text

        # Load translation prompts
        self.system_prompt, self.user_prompt, self.previous_prompt, self.previous_text_default = load_prompt(src_lang, dst_lang)
        if self.previous_text is None:
            self.previous_text = self.previous_text_default

    def extract_content_to_json(self):
        """Abstract method: Extract document content to JSON."""
        raise NotImplementedError

    def write_translated_json_to_file(self, json_path, translated_json_path):
        """Abstract method: Write the translated JSON content back to the file."""
        raise NotImplementedError

    def translate_content(self, progress_callback):
        """Translate JSON content with dynamic progress updates."""
        print("Segmenting JSON content...")
        stream_generator = stream_segment_json(
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.previous_text,
        )
        
        if stream_generator is None:
            print("Failed to generate segments.")
            return None

        print("Translating segments...")
        combined_previous_texts = []
        for segment, segment_progress in stream_generator():
            last_valid_translated_text = None

            for retry_count in range(2):
                try:
                    translated_text = translate_text(
                        segment, self.previous_text, self.model, 
                        self.system_prompt, self.user_prompt, self.previous_prompt
                    )
                    if not translated_text:
                        raise RuntimeError("Translation returned empty text.")
                    
                    compare_translation(segment, translated_text)
                    print("Segment translated successfully.")
                    
                    last_3_entries = clean_json(translated_text).splitlines()[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                    combined_previous_texts.append(translated_text)
                    break

                except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                    print(f"Error encountered: {e}. Retrying ({retry_count + 1}/2)...")
                    last_valid_translated_text = translated_text

            else:
                if last_valid_translated_text:
                    print("Saving last valid translation despite errors.")
                    combined_previous_texts.append(last_valid_translated_text)

            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                print(f"Progress: {segment_progress * 100:.2f}%")
        
        formatted_texts = []
        for json_text in combined_previous_texts:
            # Clean and remove unwanted newlines and spaces
            cleaned_text = clean_json(json_text)
            json_data = json.loads(cleaned_text)
            for key, value in json_data.items():
                formatted_texts.append(json.dumps({key: value}, ensure_ascii=False))

        # Pass the formatted key-value lines to retranslate_failed_content
        trans_result_texts = self.retranslate_failed_content(formatted_texts, progress_callback)
        cleaned_translated_texts = [clean_json(text) for text in trans_result_texts]

        print("Saving translated JSON...")
        translated_json_path = "temp/dst_translated.json"
        with open(translated_json_path, "w", encoding="utf-8") as f:
            json.dump(cleaned_translated_texts, f, ensure_ascii=False, indent=4)

        return translated_json_path

    def retranslate_failed_content(self, combined_previous_texts, progress_callback):
        failed_segments_path = "temp/dst_translated_failed.json"
        try:
            with open(failed_segments_path, "r", encoding="utf-8") as f:
                failed_segments = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Failed to load failed segments: {e}")
            return combined_previous_texts

        if failed_segments:
            print("Retrying translation for failed segments...")

        remaining_failed_segments = []  # Store segments that still fail after retries
        total_segments = len(failed_segments)

        for idx, segment in enumerate(failed_segments):
            progress = (idx + 1) / total_segments  # Calculate progress percentage
            if progress_callback:
                progress_callback(progress, desc="Detected translation errors, retrying translations...")
            new_segment = {}  # Store individual keys that fail to update
            for key, value in segment.items():
                retry_segment = json.dumps({key: value}, ensure_ascii=False)
                failed_translated_text = translate_text(
                    retry_segment,
                    self.previous_text,
                    self.model,
                    self.system_prompt,
                    self.user_prompt,
                    self.previous_prompt
                )

                if failed_translated_text:
                    updated = False
                    try:
                        cleaned_json_str = clean_json(failed_translated_text)
                        translated_content = json.loads(cleaned_json_str)

                        # Find and update the key in combined_previous_texts
                        for i, combined_text in enumerate(combined_previous_texts):
                            combined_json = json.loads(clean_json(combined_text))
                            if key in combined_json:
                                # Update the existing key-value pair
                                new_value = translated_content.get(key, "")
                                if new_value:
                                    combined_json[key] = new_value
                                    combined_previous_texts[i] = json.dumps(combined_json, ensure_ascii=False, separators=(',', ': '))
                                    updated = True
                                    print(f"Updated value for key '{key}'")
                                break

                        # If the key is not found in any entry, add it as a new line in the first entry
                        if not updated:
                            new_value = translated_content.get(key, "")
                            new_json_line = json.dumps({key: new_value}, ensure_ascii=False, separators=(',', ': '))
                            inserted = False
                            key_before = None

                            # Iterate through combined_previous_texts to find the correct insertion point
                            for i, combined_text in enumerate(combined_previous_texts):
                                combined_json = json.loads(clean_json(combined_text))
                                existing_key = next(iter(combined_json))  # Extract the key

                                if int(existing_key) > int(key):  # Insert before the first larger key
                                    combined_previous_texts.insert(i, new_json_line)
                                    inserted = True
                                    key_before = existing_key
                                    break

                            # If no suitable position was found, append at the end
                            if not inserted:
                                combined_previous_texts.append(new_json_line)

                            print(f"Segment '{key}' added as a new line after '{key_before}' if found.")

                    except json.JSONDecodeError as e:
                        print(f"Failed to decode translated content for key '{key}': {e}")
                        new_segment[key] = value
                else:
                    new_segment[key] = value

            # Store any remaining failed keys for this segment
            if new_segment:
                remaining_failed_segments.append(new_segment)

        # Save the remaining failed segments back to the file
        with open(failed_segments_path, "w", encoding="utf-8") as f:
            json.dump(remaining_failed_segments, f, ensure_ascii=False, indent=4)

        print("Updated remaining failed segments in temp/dst_translated_failed.json.")
        return combined_previous_texts

    def _clear_temp_folder(self):
        """Clear the temp folder at the beginning of each process."""
        temp_folder = "temp"
        if os.path.exists(temp_folder):
            print("Clearing temp folder...")
            shutil.rmtree(temp_folder)  # Remove all contents
        os.makedirs(temp_folder)  # Recreate the temp folder

    def process(self, file_name, file_extension, progress_callback=None):
        """Full process: Extract -> Translate -> Save"""
        # Clear temp folder
        self._clear_temp_folder()

        print("Extracting content to JSON...")
        if progress_callback:
            progress_callback(0, desc="Extracting text, please wait...")
        json_path = self.extract_content_to_json()

        print("Translating content...")
        if progress_callback:
            progress_callback(0, desc="Translating, please wait...")
        translated_json_path = self.translate_content(progress_callback)

        print("Writing translated content to file...")
        if progress_callback:
            progress_callback(0, desc="Translation completed, new file being generated...")
        self.write_translated_json_to_file(json_path, translated_json_path)

        result_folder = "result" 
        base_name = os.path.basename(file_name)
        final_output_path = os.path.join(result_folder, f"{base_name}_translated{file_extension}")
        return final_output_path