import os
import shutil
import json
from log_config import app_logger


from llmWrapper.ollama_wrapper import translate_text
from textProcessing.text_separator import stream_segment_json
from prompts.load_prompt import load_prompt
from .translation_checker import SRC_JSON_PATH, RESULT_JSON_PATH, FAILED_JSON_PATH, process_translation_results, clean_json, check_and_sort_translations


class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, src_lang, dst_lang, max_token, previous_text=None):
        self.input_file_path = input_file_path
        self.model = model
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.max_token = max_token
        self.previous_text = previous_text
        self.use_online = use_online

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
        app_logger.info("Segmenting JSON content...")
        stream_generator = stream_segment_json(
            SRC_JSON_PATH,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.previous_text,
        )
        
        if stream_generator is None:
            app_logger.warning("Failed to generate segments.")

        app_logger.info("Translating segments...")
        combined_previous_texts = []
        for segment, segment_progress in stream_generator():
            last_valid_translated_text = None

            for retry_count in range(2):
                try:
                    translated_text = translate_text(
                        segment, self.previous_text, self.model, self.use_online,
                        self.system_prompt, self.user_prompt, self.previous_prompt
                    )

                    process_translation_results(segment, translated_text)
                    
                    last_3_entries = clean_json(translated_text).splitlines()[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                    combined_previous_texts.append(translated_text)
                    break

                except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                    app_logger.warning(f"Error encountered: {e}. Retrying ({retry_count + 1}/2)...")
                    last_valid_translated_text = translated_text

            else:
                if last_valid_translated_text:
                    app_logger.warning("Saving last valid translation despite errors.")
                    combined_previous_texts.append(last_valid_translated_text)

            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")
        
        self.retranslate_failed_content(combined_previous_texts, progress_callback)

    def retranslate_failed_content(self, combined_previous_texts, progress_callback):
        app_logger.info("Retrying translation for failed segments...")
        stream_generator_failed = stream_segment_json(
            FAILED_JSON_PATH,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.previous_text
        )
        if stream_generator_failed is None:
            app_logger.info("All text has been translated.")

        if os.path.exists(FAILED_JSON_PATH):
            with open(FAILED_JSON_PATH, "w", encoding="utf-8") as f:
                f.write("[]")
            app_logger.info("Cleared temp/dst_translated_failed.json")

        for segment, segment_progress in stream_generator_failed():
            last_valid_translated_text = None
            for retry_count in range(2):
                try:
                    translated_text = translate_text(
                        segment,
                        self.previous_text,
                        self.model,
                        self.use_online,
                        self.system_prompt,
                        self.user_prompt,
                        self.previous_prompt
                    )

                    process_translation_results(segment, translated_text)
                    app_logger.info("Segment retranslated successfully.")
                    
                    last_3_entries = clean_json(translated_text).splitlines()[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                    combined_previous_texts.append(translated_text)
                    break

                except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                    app_logger.warning(f"Error encountered: {e}. Retrying ({retry_count + 1}/2)...")
                    last_valid_translated_text = translated_text

            else:
                if last_valid_translated_text:
                    app_logger.warning("Saving last valid translation despite errors.")
                    combined_previous_texts.append(last_valid_translated_text)

            if progress_callback:
                progress_callback(segment_progress, desc="Missing detected! Re-translating...")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")

    def _clear_temp_folder(self):
        temp_folder = "temp"
        if os.path.exists(temp_folder):
            app_logger.info("Clearing temp folder...")
            shutil.rmtree(temp_folder)
        os.makedirs(temp_folder)

    def process(self, file_name, file_extension, progress_callback=None):
        self._clear_temp_folder()

        app_logger.info("Extracting content to JSON...")
        if progress_callback:
            progress_callback(0, desc="Extracting text, please wait...")
        json_path = self.extract_content_to_json()

        app_logger.info("Translating content...")
        if progress_callback:
            progress_callback(0, desc="Translating, please wait...")
        self.translate_content(progress_callback)

        if progress_callback:
            progress_callback(0, desc="Checking for errors...")
        check_and_sort_translations()

        app_logger.info("Writing translated content to file...")
        if progress_callback:
            progress_callback(0, desc="Translation completed, new file being generated...")
        self.write_translated_json_to_file(json_path, RESULT_JSON_PATH)

        result_folder = "result" 
        base_name = os.path.basename(file_name)
        final_output_path = os.path.join(result_folder, f"{base_name}_translated{file_extension}")
        return final_output_path