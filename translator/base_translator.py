import os
import shutil
import json
from config.log_config import app_logger


from llmWrapper.llm_wrapper import translate_text
from textProcessing.text_separator import stream_segment_json, split_text_by_token_limit, recombine_split_jsons
from config.load_prompt import load_prompt
from .translation_checker import process_translation_results, clean_json, check_and_sort_translations

SRC_JSON_PATH = "src.json"
SRC_SPLIT_JSON_PATH = "src_split.json"
RESULT_SPLIT_JSON_PATH = "dst_translated_split.json"
FAILED_JSON_PATH = "dst_translated_failed.json"
RESULT_JSON_PATH = "dst_translated.json"

class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, max_token, max_retries, previous_text=None):
        self.input_file_path = input_file_path
        self.model = model
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.max_token = max_token
        self.previous_text = previous_text
        self.use_online = use_online
        self.api_key = api_key
        self.max_retries = max_retries
        self.translated_failed = True

        # Extract just the filename without the directory path
        filename = os.path.splitext(os.path.basename(input_file_path))[0]
        
        # Create a directory path using the filename
        self.file_dir = os.path.join("temp", filename)
        
        # Update all the JSON paths
        self.src_json_path = os.path.join(self.file_dir, SRC_JSON_PATH)
        self.src_split_json_path = os.path.join(self.file_dir, SRC_SPLIT_JSON_PATH)
        self.result_split_json_path = os.path.join(self.file_dir, RESULT_SPLIT_JSON_PATH)
        self.failed_json_path = os.path.join(self.file_dir, FAILED_JSON_PATH)
        self.result_json_path = os.path.join(self.file_dir, RESULT_JSON_PATH)
        
        # Ensure the directory exists
        os.makedirs(self.file_dir, exist_ok=True)

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
            self.src_split_json_path,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.previous_text,
        )
        
        if stream_generator is None:
            app_logger.warning("Failed to generate segments.")
            return

        app_logger.info("Translating segments...")
        combined_previous_texts = []
        for segment, segment_progress in stream_generator():
            try:
                translated_text = translate_text(
                    segment, self.previous_text, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue
                
                process_translation_results(segment, translated_text, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)
                
                cleaned_text = clean_json(translated_text)
                translated_lines = cleaned_text.splitlines()

                if len(translated_lines) >= 4:
                    last_3_entries = translated_lines[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                else:
                    app_logger.info("Translated text does not have enough lines to update previous_text, use Default ones")
                    self.previous_text = self.previous_text_default

                combined_previous_texts.append(translated_text)

            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                self._mark_segment_as_failed(segment)

            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")

    def retranslate_failed_content(self, progress_callback):
        app_logger.info("Retrying translation for failed segments (single attempt only)...")
        if not os.path.exists(self.failed_json_path):
            app_logger.info("No failed segments to retranslate. Skipping this step.")
            return False

        # Check if file is empty or contains an empty JSON array
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not data:  # If the JSON is an empty list or dict
                    app_logger.info("No failed segments to retranslate. Skipping this step.")
                    return False
            except json.JSONDecodeError:
                app_logger.error("Failed to decode JSON. Skipping this step.")
                return False

        stream_generator_failed = stream_segment_json(
            self.failed_json_path,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.previous_text
        )
        if stream_generator_failed is None:
            app_logger.info("All text has been translated.")
            return False

        # Read the original failed segments
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            original_segments = json.load(f)
        with open(self.failed_json_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        
        # Keep track of which segments we're going to process in this run
        segments_to_process = original_segments.copy()
        combined_previous_texts = []
 
        for segment, segment_progress in stream_generator_failed():
            try:
                translated_text = translate_text(
                    segment,
                    self.previous_text,
                    self.model,
                    self.use_online,
                    self.api_key,
                    self.system_prompt,
                    self.user_prompt,
                    self.previous_prompt
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue
                
                process_translation_results(segment, translated_text, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)

                # Update previous text context with last 3 lines if possible
                try:
                    last_3_entries = clean_json(translated_text).splitlines()[-4:-1]
                    self.previous_text = "\n".join(last_3_entries)
                except (IndexError, AttributeError):
                    app_logger.warning("Couldn't extract context from translation. Using default.")
                    
                combined_previous_texts.append(translated_text)
                
                # Remove this segment from segments we've processed
                if segment in segments_to_process:
                    segments_to_process.remove(segment)

            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Segment will remain in failed list.")

            if progress_callback:
                progress_callback(segment_progress, desc="Missing detected! Translating once...")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")
        return True

    def _convert_failed_segments_to_json(self, failed_segments):
        converted_json = {failed_segments["count"]: failed_segments["value"]}
        return json.dumps(converted_json, indent=4, ensure_ascii=False)

    def _clear_temp_folder(self):
        temp_folder = "temp"
        try:
            if os.path.exists(temp_folder):
                app_logger.info("Clearing temp folder...")
                shutil.rmtree(temp_folder)
        except Exception as e:
            app_logger.warning(f"Could not delete temp folder: {str(e)}. Continuing with existing folder.")
        finally:
            os.makedirs(temp_folder,exist_ok=True)
    
    def _mark_segment_as_failed(self, segment):        
        if not os.path.exists(self.failed_json_path):
            with open(self.failed_json_path, "w", encoding="utf-8") as f:
                json.dump([], f)

        with open(self.failed_json_path, "r+", encoding="utf-8") as f:
            try:
                failed_segments = json.load(f)
            except json.JSONDecodeError:
                failed_segments = []

            try:
                clean_segment = clean_json(segment)
                segment_dict = json.loads(clean_segment)
            except json.JSONDecodeError as e:
                app_logger.error(f"Failed to decode JSON segment: {segment}. Error: {e}")
                return
            for count, value in segment_dict.items():
                failed_segments.append({
                    "count": int(count),
                    "value": value.strip()
                })
            f.seek(0)
            json.dump(failed_segments, f, ensure_ascii=False, indent=4)

    def process(self, file_name, file_extension, progress_callback=None):
        self._clear_temp_folder()

        app_logger.info("Extracting content to JSON...")
        if progress_callback:
            progress_callback(0, desc="Extracting text, please wait...")
        self.extract_content_to_json(progress_callback)

        app_logger.info("Split JSON...")
        if progress_callback:
            progress_callback(0, desc="Extracting text, please wait...")
        split_text_by_token_limit(self.src_json_path)
        
        app_logger.info("Translating content...")
        if progress_callback:
            progress_callback(0, desc="Translating, please wait...")
        self.translate_content(progress_callback)

        retry_count = 0
        while retry_count < self.max_retries and self.translated_failed:
            if progress_callback:
                progress_callback(0, 
                                desc=f"Translating, attempt {retry_count+1}/{self.max_retries}...")            
            self.translated_failed = self.retranslate_failed_content(progress_callback)    
            retry_count += 1

        if progress_callback:
            progress_callback(0, desc="Checking for errors...")
        missing_counts = check_and_sort_translations(self.src_split_json_path, self.result_split_json_path)

        if progress_callback:
            progress_callback(0, desc="Recombine Split jsons...")
        recombine_split_jsons(self.src_split_json_path, self.result_split_json_path)

        app_logger.info("Writing translated content to file...")
        if progress_callback:
            progress_callback(0, desc="Translation completed, new file being generated...")
        self.write_translated_json_to_file(self.src_json_path, self.result_json_path, progress_callback)

        result_folder = "result" 
        base_name = os.path.basename(file_name)
        final_output_path = os.path.join(result_folder, f"{base_name}_translated{file_extension}")
        return final_output_path,missing_counts