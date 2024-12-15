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
    try:
        original_json = json.loads(clean_json(original_text))
        translated_json = json.loads(clean_json(translated_text))

        original_count = len(original_json)
        translated_count = len(translated_json)
        if original_count != translated_count:
            raise ValueError("The translation is inconsistent. Please reduce MAX_Tokens")
        for key, value in translated_json.items():
            if value == "":
                raise ValueError(f"Translated JSON contains empty value at key: {key}")
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
        translated_data = []
        combined_previous_texts = []
        for segment, segment_progress in stream_generator():
            retry_count = 0
            success = False

            while retry_count < 2 and not success:
                try:
                    translated_text = translate_text(
                        segment, 
                        self.previous_text, 
                        self.model, 
                        self.system_prompt, 
                        self.user_prompt, 
                        self.previous_prompt
                    )

                    if not translated_text:
                        raise RuntimeError("Translation returned empty text.")

                    translated_text = modify_json(translated_text)
                    compare_translation(segment, translated_text)
                    success = True
                    print(f"Segment translated successfully.")

                    translated_data.append({"translated_text": translated_text})
                    last_3_entries = clean_json(translated_text).splitlines()[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                    combined_previous_texts.append(translated_text)

                except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                    retry_count += 1
                    print(f"Error encountered: {e}. Retrying for round {retry_count + 1}")

            if not success:
                print(f"Failed to translate segment after 2 attempts. Skipping segment.")

            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                print(f"Progress: {segment_progress * 100:.2f}%")

        print("Saving translated JSON...")
        translated_json_path = "temp/dst_translated.json"
        with open(translated_json_path, "w", encoding="utf-8") as f:
            json.dump(combined_previous_texts, f, ensure_ascii=False, indent=4)

        return translated_json_path

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