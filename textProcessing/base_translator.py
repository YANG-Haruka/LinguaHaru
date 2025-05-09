import os
import shutil
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from config.log_config import app_logger
from .calculation_tokens import num_tokens_from_string

from llmWrapper.llm_wrapper import translate_text
from textProcessing.text_separator import stream_segment_json, split_text_by_token_limit, recombine_split_jsons
from config.load_prompt import load_prompt
from .translation_checker import process_translation_results, clean_json, check_and_sort_translations

SRC_JSON_PATH = "src.json"
SRC_SPLIT_JSON_PATH = "src_split.json"
RESULT_SPLIT_JSON_PATH = "dst_translated_split.json"
FAILED_JSON_PATH = "dst_translated_failed.json"
RESULT_JSON_PATH = "dst_translated.json"
MAX_PREVIOUS_TOKENS = 128

class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, continue_mode, max_token, max_retries, thread_count):
        self.input_file_path = input_file_path
        self.model = model
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.max_token = max_token
        self.use_online = use_online
        self.api_key = api_key
        self.max_retries = max_retries
        self.continue_mode = continue_mode
        self.translated_failed = True
        self.glossary_path = "models\Glossary.csv"
        self.num_threads = thread_count
        self.lock = Lock()
        self.last_ui_update_time = 0

        # Setup file paths
        filename = os.path.splitext(os.path.basename(input_file_path))[0]
        self.file_dir = os.path.join("temp", filename)
        
        self.src_json_path = os.path.join(self.file_dir, SRC_JSON_PATH)
        self.src_split_json_path = os.path.join(self.file_dir, SRC_SPLIT_JSON_PATH)
        self.result_split_json_path = os.path.join(self.file_dir, RESULT_SPLIT_JSON_PATH)
        self.failed_json_path = os.path.join(self.file_dir, FAILED_JSON_PATH)
        self.result_json_path = os.path.join(self.file_dir, RESULT_JSON_PATH)
        
        os.makedirs(self.file_dir, exist_ok=True)

        # Load translation prompts
        self.system_prompt, self.user_prompt, self.previous_prompt, self.previous_text_default, self.glossary_prompt = load_prompt(src_lang, dst_lang)
        self.previous_content = self.previous_text_default

    def extract_content_to_json(self):
        """Abstract method: Extract document content to JSON."""
        raise NotImplementedError

    def write_translated_json_to_file(self, json_path, translated_json_path):
        """Abstract method: Write the translated JSON content back to the file."""
        raise NotImplementedError

    def update_ui_safely(self, progress_callback, progress, desc):
        """Update UI with rate limiting to avoid overwhelming the UI thread"""
        current_time = time.time()
        if current_time - self.last_ui_update_time >= 0.1:
            try:
                if progress_callback:
                    progress_callback(progress, desc=desc)
                    self.last_ui_update_time = current_time
            except Exception as e:
                app_logger.warning(f"Error updating UI: {e}")

    def translate_content(self, progress_callback):
        app_logger.info("Segmenting JSON content...")
        all_segments = stream_segment_json(
            self.src_split_json_path,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.src_lang,
            self.dst_lang,
            self.glossary_path,
            self.continue_mode
        )
        
        if not all_segments and not self.continue_mode:
            app_logger.warning("No segments were generated.")
            return

        total_current_batch = len(all_segments)
        app_logger.info(f"Translating {total_current_batch} segments using {self.num_threads} threads...")

        # Initialize variables
        total_segments = 0
        completed_count = 0
        remaining_ratio = 1.0
        
        # Handle continue mode progress calculation
        if self.continue_mode:
            try:
                if os.path.exists(self.src_split_json_path):
                    with open(self.src_split_json_path, 'r', encoding='utf-8') as f:
                        source_content = json.load(f)
                        total_segments = len(source_content)
                else:
                    total_segments = total_current_batch
                
                if os.path.exists(self.result_split_json_path):
                    with open(self.result_split_json_path, 'r', encoding='utf-8') as f:
                        translated_content = json.load(f)
                        completed_count = len(translated_content)
                
                if total_segments > 0:
                    completed_ratio = completed_count / total_segments
                    remaining_ratio = 1.0 - completed_ratio
                    
                    self.update_ui_safely(
                        progress_callback, 
                        completed_ratio, 
                        f"Continuing translation..."
                    )
                
            except Exception as e:
                app_logger.warning(f"Could not determine previous progress: {str(e)}")
                total_segments = total_current_batch
                remaining_ratio = 1.0
        else:
            total_segments = total_current_batch
        
        def process_segment(segment_data):
            segment, _, current_glossary_terms = segment_data
            try:
                with self.lock:
                    current_previous = self.previous_content
                
                translated_text = translate_text(
                    segment, current_previous, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    with self.lock:
                        self._mark_segment_as_failed(segment)
                    return None
                
                with self.lock:
                    translation_results = process_translation_results(
                        segment, translated_text,
                        self.src_split_json_path, self.result_split_json_path, self.failed_json_path,
                        self.src_lang, self.dst_lang
                    )
                    self.previous_content = self._update_previous_content(
                        translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                    )
                return translation_results
            except Exception as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                with self.lock:
                    self._mark_segment_as_failed(segment)
                return None

        # Use thread pool for translation
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = [executor.submit(process_segment, seg) for seg in all_segments]
            
            if not self.continue_mode:
                self.update_ui_safely(progress_callback, 0.0, f"Translating...")
            
            current_batch_completed = 0
            
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    app_logger.error(f"Segment translation error: {e}")
                
                current_batch_completed += 1
                
                # Calculate overall progress
                if self.continue_mode:
                    current_batch_progress = current_batch_completed / total_current_batch
                    batch_contribution = remaining_ratio * current_batch_progress
                    overall_progress = (1.0 - remaining_ratio) + batch_contribution
                    
                    self.update_ui_safely(
                        progress_callback, 
                        overall_progress, 
                        f"Translating..."
                    )
                else:
                    p = current_batch_completed / total_current_batch
                    self.update_ui_safely(progress_callback, p, f"Translating...")

    def retranslate_failed_content(self, retry_count, max_retries, progress_callback, last_try=False):
        app_logger.info(f"Retrying translation...{retry_count}/{max_retries}")
        
        if not os.path.exists(self.failed_json_path):
            app_logger.info("No failed segments to retranslate. Skipping this step.")
            return False

        # Read and check failed list
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not data:
                    app_logger.info("No failed segments to retranslate. Skipping this step.")
                    return False
            except json.JSONDecodeError:
                app_logger.error("Failed to decode JSON. Skipping this step.")
                return False

        # Get failed segments
        all_failed_segments = stream_segment_json(
            self.failed_json_path,
            self.max_token,
            self.system_prompt,
            self.user_prompt,
            self.previous_prompt,
            self.src_lang,
            self.dst_lang,
            self.glossary_path,
            self.continue_mode
        )
        
        if not all_failed_segments:
            app_logger.info("All text has been translated.")
            return False

        # Special handling for last try - line by line translation
        if last_try and all_failed_segments:
            app_logger.info("Last try mode: processing each line individually for better success rate")
            
            processed_segments = []
            total_lines = 0
            
            # Count total lines
            for segment, _, _ in all_failed_segments:
                try:
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    total_lines += len(segment_json)
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment during count: {e}")
                    total_lines += 1
            
            app_logger.info(f"Total lines to process in last try: {total_lines}")
            current_line = 0
            
            # Split each segment into individual lines
            for segment, segment_progress, current_glossary_terms in all_failed_segments:
                try:
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    
                    for key, value in segment_json.items():
                        single_line_json = {key: value}
                        single_line_segment = f"```json\n{json.dumps(single_line_json, ensure_ascii=False, indent=4)}\n```"
                        
                        current_line += 1
                        line_progress = current_line / total_lines if total_lines > 0 else 0
                        
                        # Filter glossary terms for current line
                        line_glossary_terms = []
                        if current_glossary_terms:
                            line_glossary_terms = [term for term in current_glossary_terms if term[0] in value]
                        
                        processed_segments.append((single_line_segment, line_progress, line_glossary_terms))
                        
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment content: {e}. Keeping original segment.")
                    current_line += 1
                    processed_segments.append((segment, current_line / total_lines if total_lines > 0 else 0, current_glossary_terms))
            
            if processed_segments:
                all_failed_segments = processed_segments
                app_logger.info(f"Final attempt will process {len(processed_segments)} individual lines")
        
        # Clear failed list
        with self.lock:
            with open(self.failed_json_path, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=4)
        
        total = len(all_failed_segments)
        retry_desc = "Final translation attempt" if last_try else "Retrying translation"
        app_logger.info(f"{retry_desc} {total} segments using {self.num_threads} threads...")

        def process_failed_segment(segment_data):
            segment, _, current_glossary_terms = segment_data
            try:
                with self.lock:
                    current_previous = self.previous_content
                translated_text = translate_text(
                    segment, current_previous, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    with self.lock:
                        self._mark_segment_as_failed(segment)
                    return None

                with self.lock:
                    translation_results = process_translation_results(
                        segment, translated_text,
                        self.src_split_json_path, self.result_split_json_path,
                        self.failed_json_path, self.src_lang, self.dst_lang,
                        last_try=last_try
                    )
                    self.previous_content = self._update_previous_content(
                        translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                    )
                return translation_results
            except Exception as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                with self.lock:
                    self._mark_segment_as_failed(segment)
                return None

        # Use thread pool and update progress in main thread
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = [executor.submit(process_failed_segment, seg) for seg in all_failed_segments]
            self.update_ui_safely(progress_callback, 0.0, f"{retry_desc}...")

            for idx, future in enumerate(as_completed(futures), start=1):
                try:
                    future.result()
                except Exception as e:
                    app_logger.error(f"Failed segment error: {e}")
                p = idx / total
                self.update_ui_safely(progress_callback, p, f"{retry_desc}...{retry_count+1}/{max_retries}")

        self.update_ui_safely(progress_callback, 1.0, f"{retry_desc} completed.")
        return True

    def _update_previous_content(self, translated_text_dict, previous_content, max_tokens):
        """Update context, keeping most recent translated segments within token limit"""
        if not translated_text_dict:
            return previous_content
        
        sorted_items = sorted(translated_text_dict.items(), key=lambda x: x[0])
        valid_items = [(k, v) for k, v in sorted_items if v and len(v.strip()) > 1]
        
        if not valid_items:
            return previous_content
        
        # Keep only last three segments
        if len(valid_items) > 3:
            valid_items = valid_items[-3:]
        
        total_tokens = sum(num_tokens_from_string(v) for _, v in valid_items)
        
        if total_tokens > max_tokens and len(valid_items) == 1:
            app_logger.info(f"Single paragraph exceeds token limit: {total_tokens} tokens > {max_tokens}")
            return previous_content
        
        if total_tokens > max_tokens:
            final_items = []
            current_tokens = 0
            
            for item in reversed(valid_items):
                k, v = item
                v_tokens = num_tokens_from_string(v)
                
                if current_tokens + v_tokens > max_tokens:
                    if not final_items:
                        app_logger.info(f"Cannot fit any paragraph within token limit")
                        return previous_content
                    break
                
                final_items.insert(0, item)
                current_tokens += v_tokens
            
            valid_items = final_items
        
        new_content = {}
        for k, v in valid_items:
            new_content[k] = v
        
        app_logger.debug(f"New previous_content: {len(valid_items)} paragraphs, {total_tokens} tokens")
        
        return new_content
    
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
        # Protect file access with lock
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
        # Check if using continue mode
        if self.continue_mode:
            translated_count = 0
            total_count = 0
            
            try:
                if os.path.exists(self.result_split_json_path):
                    with open(self.result_split_json_path, 'r', encoding='utf-8') as f:
                        translated_content = json.load(f)
                        translated_count = len(translated_content)
                
                if os.path.exists(self.src_split_json_path):
                    with open(self.src_split_json_path, 'r', encoding='utf-8') as f:
                        source_content = json.load(f)
                        total_count = len(source_content)
                        
                if total_count > 0 and progress_callback:
                    current_progress = min(1.0, translated_count / total_count)
                    self.update_ui_safely(
                        progress_callback, 
                        current_progress, 
                        f"Continuing from previous progress ({translated_count}/{total_count})"
                    )
                    app_logger.info(f"Continuing from previous progress: {translated_count}/{total_count} ({current_progress:.1%})")
            except Exception as e:
                app_logger.warning(f"Could not determine previous progress: {str(e)}")
                self.update_ui_safely(progress_callback, 0, "Continuing translation...")
        else:
            self._clear_temp_folder()

            app_logger.info("Extracting content to JSON...")
            self.update_ui_safely(progress_callback, 0, "Extracting text, please wait...")
            self.extract_content_to_json(progress_callback)

            app_logger.info("Split JSON...")
            self.update_ui_safely(progress_callback, 0, "Splitting text into segments...")
            split_text_by_token_limit(self.src_json_path)
        
        app_logger.info("Translating content...")
        self.update_ui_safely(progress_callback, 0, "Translating, please wait...")
        self.translate_content(progress_callback)

        # Handle retries for failed translations
        retry_count = 0
        while retry_count < self.max_retries and self.translated_failed:
            is_last_try = (retry_count == self.max_retries - 1)
            self.translated_failed = self.retranslate_failed_content(
                retry_count, 
                self.max_retries, 
                progress_callback, 
                last_try=is_last_try
            )
            retry_count += 1

        self.update_ui_safely(progress_callback, 0, "Checking for errors...")
        missing_counts = check_and_sort_translations(self.src_split_json_path, self.result_split_json_path)

        self.update_ui_safely(progress_callback, 0, "Recombining segments...")
        recombine_split_jsons(self.src_split_json_path, self.result_split_json_path)

        app_logger.info("Writing translated content to file...")
        self.update_ui_safely(progress_callback, 0, "Translation completed, generating output file...")
        self.write_translated_json_to_file(self.src_json_path, self.result_json_path, progress_callback)

        # Ensure final progress shows 100%
        self.update_ui_safely(progress_callback, 1.0, "Translation completed successfully")

        result_folder = "result" 
        base_name = os.path.basename(file_name)
        final_output_path = os.path.join(result_folder, f"{base_name}_translated{file_extension}")
        return final_output_path, missing_counts