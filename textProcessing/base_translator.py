import os
import shutil
import json
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from config.log_config import app_logger
from .calculation_tokens import num_tokens_from_string
from config.translation_history import TranslationHistoryManager, create_translation_record

from llmWrapper.llm_wrapper import translate_text, interruptible_sleep
from textProcessing.text_separator import (
    stream_segment_json, split_text_by_token_limit,
    deduplicate_translation_content, create_deduped_json_for_translation,
    restore_translations_from_deduped
)
from config.load_prompt import load_prompt
from config.languages_config import LABEL_TRANSLATIONS
from .translation_checker import process_translation_results, clean_json, check_and_sort_translations

# File path constants
SRC_JSON_PATH = "src.json"
SRC_DEDUPED_JSON_PATH = "src_deduped.json"
SRC_SPLIT_JSON_PATH = "src_deduped_split.json"
RESULT_SPLIT_JSON_PATH = "dst_translated_split.json"
FAILED_JSON_PATH = "dst_translated_failed.json"
RESULT_JSON_PATH = "dst_translated.json"
MAX_PREVIOUS_TOKENS = 128

class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, continue_mode, max_token, max_retries, thread_count, glossary_path, temp_dir, result_dir, session_lang="en", log_dir="log"):
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
        self.glossary_path = glossary_path
        self.num_threads = thread_count
        self.lock = Lock()
        self.check_stop_requested = None
        self.last_ui_update_time = 0
        self.temp_dir = temp_dir
        self.result_dir = result_dir
        self.session_lang = session_lang
        self.log_dir = log_dir

        # Translation history tracking
        self.translation_id = str(uuid.uuid4())
        self.translation_start_time = None
        self.translation_end_time = None

        # Token usage tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0

        # Setup file paths
        filename = os.path.splitext(os.path.basename(input_file_path))[0]
        self.file_dir = os.path.join(self.temp_dir, filename)
        
        # File paths
        self.src_json_path = os.path.join(self.file_dir, SRC_JSON_PATH)
        self.src_deduped_json_path = os.path.join(self.file_dir, SRC_DEDUPED_JSON_PATH)
        self.src_split_json_path = os.path.join(self.file_dir, SRC_SPLIT_JSON_PATH)
        self.result_split_json_path = os.path.join(self.file_dir, RESULT_SPLIT_JSON_PATH)
        self.failed_json_path = os.path.join(self.file_dir, FAILED_JSON_PATH)
        self.result_json_path = os.path.join(self.file_dir, RESULT_JSON_PATH)
        
        # Deduplication mapping
        self.count_src_to_deduped_map = None
        
        os.makedirs(self.file_dir, exist_ok=True)

        # Load prompts
        self.system_prompt, self.user_prompt, self.previous_prompt, self.previous_text_default, self.glossary_prompt = load_prompt(src_lang, dst_lang)
        self.previous_content = self.previous_text_default

    def check_for_stop(self):
        """Check if translation should stop"""
        if self.check_stop_requested and callable(self.check_stop_requested):
            self.check_stop_requested()

    def _get_status_message(self, key, **kwargs):
        """Get translated status message"""
        labels = LABEL_TRANSLATIONS.get(self.session_lang, LABEL_TRANSLATIONS.get("en", {}))
        message = labels.get(key, key)
        if kwargs:
            try:
                return message.format(**kwargs)
            except (KeyError, ValueError):
                return message
        return message

    def _get_language_display_name(self, lang_code):
        """Get display name for a language code"""
        # Reverse lookup in LANGUAGE_MAP
        from config.languages_config import LANGUAGE_MAP
        for display_name, code in LANGUAGE_MAP.items():
            if code == lang_code:
                return display_name
        # If not found, return the code itself
        return lang_code

    def _get_current_log_file_path(self):
        """Get the current log file path"""
        from config.log_config import file_logger
        if hasattr(file_logger, 'current_log_file') and file_logger.current_log_file:
            return file_logger.current_log_file
        # Fallback: construct a reasonable path
        input_filename = os.path.basename(self.input_file_path)
        return os.path.join(self.log_dir, f"{input_filename}.log")

    def _save_translation_summary(self, status, output_file_path=None):
        """Save translation summary to history"""
        try:
            self.translation_end_time = datetime.now()

            # Get log file path
            log_file_path = self._get_current_log_file_path()

            # Get display names for languages
            src_lang_display = self._get_language_display_name(self.src_lang)
            dst_lang_display = self._get_language_display_name(self.dst_lang)

            # Get input filename
            input_file = os.path.basename(self.input_file_path)

            # Create record
            record = create_translation_record(
                translation_id=self.translation_id,
                start_time=self.translation_start_time,
                end_time=self.translation_end_time,
                total_tokens=self.total_tokens,
                src_lang=self.src_lang,
                src_lang_display=src_lang_display,
                dst_lang=self.dst_lang,
                dst_lang_display=dst_lang_display,
                model=self.model,
                use_online=self.use_online,
                input_file=input_file,
                output_file_path=output_file_path or "",
                log_file_path=log_file_path,
                status=status
            )

            # Save to history
            history_manager = TranslationHistoryManager(log_dir=self.log_dir)
            history_manager.add_record(record)

            app_logger.info(f"Translation summary saved: {status}, tokens: {self.total_tokens}")
        except Exception as e:
            app_logger.error(f"Error saving translation summary: {e}")

    def save_failed_summary(self):
        """Save translation summary with failed status - called from app.py on error"""
        if self.translation_start_time:
            self._save_translation_summary(status="failed")

    def save_stopped_summary(self):
        """Save translation summary with stopped status - called from app.py when user stops"""
        if self.translation_start_time:
            self._save_translation_summary(status="stopped")

    def extract_content_to_json(self):
        """Extract document content to JSON - to be implemented by subclass"""
        raise NotImplementedError

    def write_translated_json_to_file(self, json_path, translated_json_path):
        """Write translated JSON to file - to be implemented by subclass"""
        raise NotImplementedError

    def _add_token_usage(self, token_usage):
        """Add token usage to running totals (thread-safe)"""
        if token_usage:
            with self.lock:
                self.total_prompt_tokens += token_usage.get('prompt_tokens', 0)
                self.total_completion_tokens += token_usage.get('completion_tokens', 0)
                self.total_tokens += token_usage.get('total_tokens', 0)

    def _format_tokens(self, tokens):
        """Format token count with K suffix for thousands"""
        if tokens >= 1000:
            return f"{tokens / 1000:.1f}K"
        return str(tokens)

    def _get_token_display(self):
        """Get formatted token display string"""
        if self.total_tokens > 0:
            return f" | Tokens: {self._format_tokens(self.total_tokens)}"
        return ""

    def update_ui_safely(self, progress_callback, progress, desc):
        """Update UI with rate limiting"""
        self.check_for_stop()

        current_time = time.time()
        if current_time - self.last_ui_update_time >= 0.1:
            try:
                if progress_callback:
                    # Append token count to description
                    display_desc = f"{desc}{self._get_token_display()}"
                    progress_callback(progress, desc=display_desc)
                    self.last_ui_update_time = current_time
            except Exception as e:
                app_logger.warning(f"Error updating UI: {e}")

    def translate_content(self, progress_callback):
        self.check_for_stop()
        app_logger.info("Segmenting JSON content...")
        
        # Get segments to translate
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

        # Progress calculation
        total_segments = 0
        completed_count = 0
        remaining_ratio = 1.0
        
        # Calculate progress for continue mode
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
            """Process a single segment with retry logic"""
            segment, segment_progress, current_glossary_terms = segment_data
            
            # Retry limits
            max_retry_time = 3600  # 1 hour
            max_empty_retries = 1  # 1 retry for empty results
            start_time = time.time()
            retry_count = 0
            empty_result_count = 0
            
            while True:
                self.check_for_stop()
                retry_count += 1
                
                try:
                    with self.lock:
                        current_previous = self.previous_content

                    # Translate with stop callback
                    translated_text, success, token_usage = translate_text(
                        segment, current_previous, self.model, self.use_online, self.api_key,
                        self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt,
                        current_glossary_terms, check_stop_callback=self.check_for_stop
                    )

                    # Track token usage
                    self._add_token_usage(token_usage)

                    # Handle failure
                    if not success:
                        elapsed_time = time.time() - start_time
                        remaining_time = max_retry_time - elapsed_time
                        
                        if remaining_time <= 0:
                            app_logger.error(f"Segment translation failed after 1 hour ({retry_count} attempts)")
                            self._mark_segment_as_failed(segment)
                            return None
                        
                        app_logger.warning(f"Segment translation failed (attempt {retry_count})")
                        interruptible_sleep(min(1, remaining_time), self.check_for_stop)
                        continue
                    
                    # Handle empty result
                    if not translated_text:
                        empty_result_count += 1
                        if empty_result_count > max_empty_retries:
                            app_logger.error(f"Segment returned empty result {max_empty_retries} times")
                            self._mark_segment_as_failed(segment)
                            return None
                        
                        app_logger.warning(f"Segment returned empty result (attempt {empty_result_count}/{max_empty_retries})")
                        interruptible_sleep(1, self.check_for_stop)
                        continue
                    
                    # Process successful translation
                    with self.lock:
                        translation_results = process_translation_results(
                            segment, translated_text,
                            self.src_split_json_path, self.result_split_json_path, self.failed_json_path,
                            self.src_lang, self.dst_lang
                        )
                        
                        if translation_results:
                            self.previous_content = self._update_previous_content(
                                translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                            )
                            return translation_results
                        else:
                            empty_result_count += 1
                            if empty_result_count > max_empty_retries:
                                app_logger.warning(f"Failed to process results {max_empty_retries} times")
                                self._mark_segment_as_failed(segment)
                                return None
                            
                            app_logger.warning("Failed to process translation results")
                            interruptible_sleep(1, self.check_for_stop)
                            continue
                
                except Exception as e:
                    elapsed_time = time.time() - start_time
                    remaining_time = max_retry_time - elapsed_time
                    
                    if remaining_time <= 0:
                        app_logger.error(f"Error processing segment after 1 hour: {e}")
                        self._mark_segment_as_failed(segment)
                        return None
                    
                    app_logger.warning(f"Error processing segment: {e}")
                    interruptible_sleep(min(1, remaining_time), self.check_for_stop)
                    continue

        # Translate segments in parallel
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = []
            for seg in all_segments:
                future = executor.submit(process_segment, seg)
                futures.append(future)
            
            if not self.continue_mode:
                self.update_ui_safely(progress_callback, 0.0, f"{self._get_status_message('Translating')}...")

            current_batch_completed = 0

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    app_logger.error(f"Segment translation error: {e}")

                current_batch_completed += 1

                # Update progress
                if self.continue_mode:
                    current_batch_progress = current_batch_completed / total_current_batch
                    batch_contribution = remaining_ratio * current_batch_progress
                    overall_progress = (1.0 - remaining_ratio) + batch_contribution
                    app_logger.info(f"Progress: {overall_progress:.2%}")
                    self.update_ui_safely(
                        progress_callback,
                        overall_progress,
                        f"{self._get_status_message('Translating')}..."
                    )
                else:
                    p = current_batch_completed / total_current_batch
                    app_logger.info(f"Progress: {p:.2%}")
                    self.update_ui_safely(progress_callback, p, f"{self._get_status_message('Translating')}...")

    def retranslate_failed_content(self, retry_count, max_retries, progress_callback, last_try=False):
        self.check_for_stop()
        app_logger.info(f"Retrying failed translations...{retry_count}/{max_retries}")
        
        if not os.path.exists(self.failed_json_path):
            app_logger.info("No failed segments to retranslate")
            return False

        # Read failed list
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not data:
                    app_logger.info("No failed segments to retranslate")
                    return False
            except json.JSONDecodeError:
                app_logger.error("Failed to decode JSON")
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
            app_logger.info("All text has been translated")
            return False

        # Last try - process line by line
        if last_try and all_failed_segments:
            app_logger.info("Last try: processing each line individually")
            
            processed_segments = []
            total_lines = 0
            
            # Count total lines
            for segment, _, _ in all_failed_segments:
                try:
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    total_lines += len(segment_json)
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment: {e}")
                    total_lines += 1
            
            app_logger.info(f"Total lines to process: {total_lines}")
            current_line = 0
            
            # Split segments into individual lines
            for segment, segment_progress, current_glossary_terms in all_failed_segments:
                try:
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    
                    for key, value in segment_json.items():
                        single_line_json = {key: value}
                        single_line_segment = f"```json\n{json.dumps(single_line_json, ensure_ascii=False, indent=4)}\n```"
                        
                        current_line += 1
                        line_progress = current_line / total_lines if total_lines > 0 else 0
                        
                        # Filter glossary terms
                        line_glossary_terms = []
                        if current_glossary_terms:
                            line_glossary_terms = [term for term in current_glossary_terms if term[0] in value]
                        
                        processed_segments.append((single_line_segment, line_progress, line_glossary_terms))
                        
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment: {e}")
                    current_line += 1
                    processed_segments.append((segment, current_line / total_lines if total_lines > 0 else 0, current_glossary_terms))
            
            if processed_segments:
                all_failed_segments = processed_segments
                app_logger.info(f"Processing {len(processed_segments)} individual lines")
        
        # Clear failed list
        with self.lock:
            with open(self.failed_json_path, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=4)
        
        total = len(all_failed_segments)
        retry_desc = f"{self._get_status_message('Retry')}"
        app_logger.info(f"Retrying {total} segments using {self.num_threads} threads...")

        def process_failed_segment(segment_data, last_try=False):
            """Process failed segment with retry logic"""
            segment, segment_progress, current_glossary_terms = segment_data
            
            # Retry limits
            max_retry_time = 3600  # 1 hour
            max_empty_retries = 1  # 1 retry
            start_time = time.time()
            retry_count = 0
            empty_result_count = 0
            
            while True:
                self.check_for_stop()
                retry_count += 1
                
                try:
                    with self.lock:
                        current_previous = self.previous_content

                    # Translate
                    translated_text, success, token_usage = translate_text(
                        segment, current_previous, self.model, self.use_online, self.api_key,
                        self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt,
                        current_glossary_terms, check_stop_callback=self.check_for_stop
                    )

                    # Track token usage
                    self._add_token_usage(token_usage)

                    # Handle failure
                    if not success:
                        elapsed_time = time.time() - start_time
                        remaining_time = max_retry_time - elapsed_time

                        if remaining_time <= 0:
                            app_logger.error(f"Failed segment translation failed after 1 hour")
                            self._mark_segment_as_failed(segment)
                            return None

                        app_logger.warning(f"Failed segment translation failed (attempt {retry_count})")
                        interruptible_sleep(min(1, remaining_time), self.check_for_stop)
                        continue

                    # Handle empty result
                    if not translated_text:
                        empty_result_count += 1
                        if empty_result_count > max_empty_retries:
                            app_logger.error(f"Failed segment returned empty result")
                            self._mark_segment_as_failed(segment)
                            return None
                        
                        app_logger.warning(f"Failed segment returned empty result")
                        interruptible_sleep(1, self.check_for_stop)
                        continue
                    
                    # Process successful translation
                    with self.lock:
                        translation_results = process_translation_results(
                            segment, translated_text,
                            self.src_split_json_path, self.result_split_json_path,
                            self.failed_json_path, self.src_lang, self.dst_lang,
                            last_try=last_try
                        )
                        
                        if translation_results:
                            self.previous_content = self._update_previous_content(
                                translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                            )
                            app_logger.debug(f"Successfully processed segment")
                            return translation_results
                        else:
                            empty_result_count += 1
                            app_logger.warning(f"Failed to process translation results")
                            
                            if empty_result_count > max_empty_retries:
                                app_logger.warning(f"Failed to process results")
                                self._mark_segment_as_failed(segment)
                                return None
                            
                            interruptible_sleep(1, self.check_for_stop)
                            continue
                
                except Exception as e:
                    elapsed_time = time.time() - start_time
                    remaining_time = max_retry_time - elapsed_time
                    
                    if remaining_time <= 0:
                        app_logger.error(f"Error processing failed segment: {e}")
                        self._mark_segment_as_failed(segment)
                        return None
                    
                    app_logger.warning(f"Error processing failed segment: {e}")
                    interruptible_sleep(min(1, remaining_time), self.check_for_stop)
                    continue

        # Process failed segments in parallel
        with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = []
            for seg in all_failed_segments:
                future = executor.submit(process_failed_segment, seg, last_try)
                futures.append(future)
            
            self.update_ui_safely(progress_callback, 0.0, f"{retry_desc}...")
            
            completed = 0
            failed_count = 0
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result is None:
                        failed_count += 1
                        app_logger.debug(f"Segment processing failed")
                except Exception as e:
                    failed_count += 1
                    app_logger.error(f"Failed segment error: {e}")
                
                completed += 1
                p = completed / total
                app_logger.info(f"Progress: {p:.2%}")
                self.update_ui_safely(
                    progress_callback, 
                    p, 
                    f"{retry_desc}...{retry_count+1}/{max_retries}"
                )

        self.update_ui_safely(progress_callback, 1.0, f"{retry_desc} completed")
        
        # Check if any segments remain failed
        try:
            if os.path.exists(self.failed_json_path):
                with open(self.failed_json_path, 'r', encoding='utf-8') as f:
                    remaining_failed = json.load(f)
                    if remaining_failed:
                        return True
        except Exception as e:
            app_logger.error(f"Error checking failed list: {e}")
        
        return False

    def _update_previous_content(self, translated_text_dict, previous_content, max_tokens):
        """Update context with recent translations"""
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
            app_logger.info(f"Single paragraph exceeds token limit: {total_tokens} > {max_tokens}")
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
    
    def _clear_temp_folder(self):
        """Clear temp folder"""
        temp_folder = self.temp_dir
        try:
            if os.path.exists(temp_folder):
                app_logger.info("Clearing temp folder...")
                shutil.rmtree(temp_folder)
        except Exception as e:
            app_logger.warning(f"Could not delete temp folder: {str(e)}")
        finally:
            os.makedirs(temp_folder, exist_ok=True)
    
    def _mark_segment_as_failed(self, segment):
        """Mark segment as failed"""
        app_logger.debug(f"Marking segment as failed")
        
        # Ensure file exists
        if not os.path.exists(self.failed_json_path):
            try:
                with open(self.failed_json_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
                app_logger.debug(f"Created failed segments file")
            except Exception as e:
                app_logger.error(f"Error creating failed segments file: {e}")
                return

        # Update file with locking
        try:
            with open(self.failed_json_path, "r+", encoding="utf-8") as f:
                try:
                    failed_segments = json.load(f)
                except json.JSONDecodeError:
                    failed_segments = []
                    app_logger.warning("Failed segments file was corrupted")

                try:
                    clean_segment = clean_json(segment)
                    segment_dict = json.loads(clean_segment)
                except json.JSONDecodeError as e:
                    app_logger.error(f"Failed to decode JSON segment: {e}")
                    return
                    
                for count_split, value in segment_dict.items():
                    failed_segments.append({
                        "count_split": int(count_split), 
                        "value": value.strip()
                    })
                    
                f.seek(0)
                f.truncate()
                json.dump(failed_segments, f, ensure_ascii=False, indent=4)
                app_logger.debug(f"Saved {len(segment_dict)} items to failed list")
                
        except Exception as e:
            app_logger.error(f"Error updating failed segments: {e}")
    
    def process(self, file_name, file_extension, progress_callback=None):
        """Main processing method"""

        # Record translation start time
        self.translation_start_time = datetime.now()

        # Continue mode
        if self.continue_mode:
            app_logger.info("Continue mode: checking existing files...")
            
            # Check source JSON
            if not os.path.exists(self.src_json_path):
                app_logger.info("Source JSON not found, extracting content...")
                self.extract_content_to_json(progress_callback)
            
            # Check deduped files
            if not os.path.exists(self.src_deduped_json_path):
                app_logger.info("Deduped files not found, deduplicating...")
                self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Preparing content')}...")

                deduped_data, self.count_src_to_deduped_map = deduplicate_translation_content(self.src_json_path)
                create_deduped_json_for_translation(deduped_data, self.src_deduped_json_path)
            else:
                # Reconstruct mapping
                deduped_data, self.count_src_to_deduped_map = deduplicate_translation_content(self.src_json_path)

            # Check split files
            if not os.path.exists(self.src_split_json_path):
                app_logger.info("Split files not found, splitting content...")
                self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Splitting text')}...")
                split_text_by_token_limit(self.src_deduped_json_path)

            # Create result file if missing
            if not os.path.exists(self.result_split_json_path):
                with open(self.result_split_json_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=4)

            # Calculate progress
            try:
                with open(self.src_split_json_path, 'r', encoding='utf-8') as f:
                    total_segments = len(json.load(f))
                with open(self.result_split_json_path, 'r', encoding='utf-8') as f:
                    completed_segments = len(json.load(f))

                if total_segments > 0:
                    progress = completed_segments / total_segments
                    self.update_ui_safely(progress_callback, progress, f"{self._get_status_message('Continuing from')} {progress:.1%}...")
                    app_logger.info(f"Continue mode: {completed_segments}/{total_segments} segments ({progress:.1%})")
            except Exception as e:
                app_logger.warning(f"Could not calculate progress: {e}")
        else:
            # Fresh start
            self._clear_temp_folder()

            app_logger.info("Extracting content...")
            self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Extracting text')}...")
            self.extract_content_to_json(progress_callback)

            app_logger.info("Deduplicating content...")
            self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Removing duplicates')}...")
            deduped_data, self.count_src_to_deduped_map = deduplicate_translation_content(self.src_json_path)
            create_deduped_json_for_translation(deduped_data, self.src_deduped_json_path)

            app_logger.info("Splitting content...")
            self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Splitting text')}...")
            split_text_by_token_limit(self.src_deduped_json_path)

        # Main translation
        app_logger.info("Starting translation...")
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Translating content')}...")
        self.translate_content(progress_callback)

        # Retry failed translations
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

        # Post-processing
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Checking results')}...")
        missing_counts = check_and_sort_translations(self.src_split_json_path, self.result_split_json_path)

        # Restore to original structure
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Restoring structure')}...")
        app_logger.info("Restoring translations...")
        restore_translations_from_deduped(
            self.result_split_json_path,
            self.count_src_to_deduped_map,
            self.src_json_path
        )

        # Write output
        app_logger.info("Writing output...")
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Generating output')}...")
        self.write_translated_json_to_file(self.src_json_path, self.result_json_path, progress_callback)

        # Complete - show total tokens used
        completion_msg = self._get_status_message('Translation completed')
        tokens_msg = self._get_status_message('Total tokens used')
        if self.total_tokens > 0:
            completion_msg = f"{completion_msg} | {tokens_msg}: {self._format_tokens(self.total_tokens)}"
        self.update_ui_safely(progress_callback, 1.0, completion_msg)

        # Return output path
        result_folder = self.result_dir
        base_name = os.path.basename(file_name)
        # Use source_lang2target_lang format (e.g., zh2ja)
        lang_suffix = f"{self.src_lang}2{self.dst_lang}"
        final_output_path = os.path.join(result_folder, f"{base_name}_{lang_suffix}{file_extension}")

        # Save translation summary
        self._save_translation_summary(status="success", output_file_path=final_output_path)

        return final_output_path, missing_counts