import os
import shutil
import json
import concurrent.futures
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
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, continue_mode, max_token, max_retries, num_threads=4):
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
        self.num_threads = num_threads  # 添加线程数量参数
        self.lock = Lock()  # 添加锁，用于保护共享资源

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
        self.system_prompt, self.user_prompt, self.previous_prompt, self.previous_text_default, self.glossary_prompt = load_prompt(src_lang, dst_lang)
        self.previous_content = self.previous_text_default  # 使用实例变量而不是全局变量

    def extract_content_to_json(self):
        """Abstract method: Extract document content to JSON."""
        raise NotImplementedError

    def write_translated_json_to_file(self, json_path, translated_json_path):
        """Abstract method: Write the translated JSON content back to the file."""
        raise NotImplementedError

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

        app_logger.info(f"Translating {len(all_segments)} segments using {self.num_threads} threads...")
        
        # 创建共享状态来跟踪进度
        progress_state = {
            "completed": 0,
            "total": len(all_segments),
            "last_progress": 0
        }
        
        # 处理单个段落的函数
        def process_segment(segment_data):
            segment, segment_progress, current_glossary_terms = segment_data
            try:
                # 复制当前的前文上下文，避免并发问题
                with self.lock:
                    current_previous_content = self.previous_content
                
                translated_text = translate_text(
                    segment, current_previous_content, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    with self.lock:
                        self._mark_segment_as_failed(segment)
                    return None
                
                with self.lock:  # 使用锁保护共享资源
                    translation_results = process_translation_results(
                        segment, translated_text, self.src_split_json_path, 
                        self.result_split_json_path, self.failed_json_path, 
                        self.src_lang, self.dst_lang
                    )
                    # 更新前文上下文
                    self.previous_content = self._update_previous_content(translation_results, self.previous_content, MAX_PREVIOUS_TOKENS)
                    
                    # 更新进度
                    progress_state["completed"] += 1
                    current_progress = progress_state["completed"] / progress_state["total"]
                    
                    progress_state["last_progress"] = current_progress
                    if progress_callback:
                        progress_callback(current_progress, desc=f"Translating... ({progress_state['completed']}/{progress_state['total']})")
                        app_logger.info(f"Progress: {current_progress * 100:.2f}%")
                    
                    return translation_results
            
            except Exception as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                with self.lock:
                    self._mark_segment_as_failed(segment)
                return None
        
        # 使用线程池执行翻译
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = [executor.submit(process_segment, segment_data) for segment_data in all_segments]
            concurrent.futures.wait(futures)
        
        # 确保最终进度显示为100%
        if progress_callback:
            progress_callback(1.0, desc=f"Translation completed. ({progress_state['total']}/{progress_state['total']})")

    def retranslate_failed_content(self, retry_count, max_retries, progress_callback, last_try=False):
        app_logger.info(f"Retrying translation...{retry_count}/{max_retries}")
        
        if not os.path.exists(self.failed_json_path):
            app_logger.info("No failed segments to retranslate. Skipping this step.")
            return False

        # Check if file is empty or contains empty JSON array
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not data:  # If JSON is an empty list or dict
                    app_logger.info("No failed segments to retranslate. Skipping this step.")
                    return False
            except json.JSONDecodeError:
                app_logger.error("Failed to decode JSON. Skipping this step.")
                return False

        # Get all failed segments
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

        # Clear failed file to prepare for new failed segments
        with self.lock:
            with open(self.failed_json_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
        
        app_logger.info(f"Retranslating {len(all_failed_segments)} failed segments using {self.num_threads} threads...")
        
        # For last attempt, process line by line for better success rate
        if last_try and all_failed_segments:
            app_logger.info("Last try mode: processing each line individually for better success rate")
            
            processed_segments = []
            total_lines = 0
            for segment, _, current_glossary_terms in all_failed_segments:
                try:
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    total_lines += len(segment_json)
                except (json.JSONDecodeError, ValueError) as e:
                    total_lines += 1
            
            current_line = 0
            for segment, segment_progress, current_glossary_terms in all_failed_segments:
                try:
                    # Parse JSON content in the segment
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    
                    # Process each line separately
                    for key, value in segment_json.items():
                        single_line_json = {key: value}
                        single_line_segment = f"```json\n{json.dumps(single_line_json, ensure_ascii=False, indent=4)}\n```"
                        
                        current_line += 1
                        line_progress = current_line / total_lines
                        line_glossary_terms = []
                        if current_glossary_terms:
                            line_glossary_terms = [term for term in current_glossary_terms if term[0] in value]
                        processed_segments.append((single_line_segment, line_progress, line_glossary_terms))
                        
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment content: {e}. Keeping original segment.")
                    current_line += 1
                    processed_segments.append((segment, current_line / total_lines, current_glossary_terms))
            
            # Update processing queue
            if processed_segments:
                all_failed_segments = processed_segments
                app_logger.info(f"Split into {len(all_failed_segments)} individual lines for processing")
        
        # 创建共享状态来跟踪进度
        progress_state = {
            "completed": 0,
            "total": len(all_failed_segments),
            "last_progress": 0
        }
        
        # 处理单个失败段落的函数
        def process_failed_segment(segment_data):
            segment, segment_progress, current_glossary_terms = segment_data
            try:
                # 复制当前的前文上下文，避免并发问题
                with self.lock:
                    current_previous_content = self.previous_content
                
                translated_text = translate_text(
                    segment, current_previous_content, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    with self.lock:
                        self._mark_segment_as_failed(segment)
                    return None

                with self.lock:  # 使用锁保护共享资源
                    # 处理翻译结果
                    translation_results = process_translation_results(
                        segment, translated_text, self.src_split_json_path, 
                        self.result_split_json_path, self.failed_json_path, 
                        self.src_lang, self.dst_lang, last_try=last_try
                    )
                    
                    # 更新前文上下文
                    self.previous_content = self._update_previous_content(translation_results, self.previous_content, MAX_PREVIOUS_TOKENS)
                    
                    # 更新进度
                    progress_state["completed"] += 1
                    current_progress = progress_state["completed"] / progress_state["total"]
                    
                    progress_state["last_progress"] = current_progress
                    if progress_callback:
                        retry_desc = "Final translation attempt" if last_try else "Retrying translation"
                        progress_callback(
                            current_progress, 
                            desc=f"{retry_desc}...{retry_count+1}/{max_retries} ({progress_state['completed']}/{progress_state['total']})"
                        )
                        app_logger.info(f"Progress: {current_progress * 100:.2f}%")
                    
                    return translation_results
            
            except Exception as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                with self.lock:
                    self._mark_segment_as_failed(segment)
                return None
        
        # 使用线程池执行翻译
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as executor:
            futures = [executor.submit(process_failed_segment, segment_data) for segment_data in all_failed_segments]
            concurrent.futures.wait(futures)
        
        # 确保最终进度显示为100%
        if progress_callback:
            retry_desc = "Final translation attempt" if last_try else "Retrying translation"
            progress_callback(1.0, desc=f"{retry_desc} completed. ({progress_state['total']}/{progress_state['total']})")
        
        return True

    def _update_previous_content(self, translated_text_dict, previous_content, max_tokens):
        """
        Update the previous context, keeping at most three translated segments with total tokens under max_tokens
        """
        # Check input validity
        if not translated_text_dict:
            return previous_content
        
        # Sort dictionary items by key and convert to list of (key, value) pairs
        sorted_items = sorted(translated_text_dict.items(), key=lambda x: x[0])
        
        # Filter out empty or invalid values
        valid_items = [(k, v) for k, v in sorted_items if v and len(v.strip()) > 1]
        
        # Return original content if no valid items
        if not valid_items:
            return previous_content
        
        # Keep only the last three segments (newest three)
        if len(valid_items) > 3:
            valid_items = valid_items[-3:]
        
        # Calculate total token count for all segments
        total_tokens = sum(num_tokens_from_string(v) for _, v in valid_items)
        
        # If total tokens exceed limit and only one segment, return original previous_content
        if total_tokens > max_tokens and len(valid_items) == 1:
            app_logger.info(f"Single paragraph exceeds token limit: {total_tokens} tokens > {max_tokens}")
            return previous_content
        
        # If total tokens exceed limit, trim segments
        if total_tokens > max_tokens:
            # Try to keep segments from newest to oldest
            final_items = []
            current_tokens = 0
            
            # Start adding from newest segment
            for item in reversed(valid_items):
                k, v = item
                v_tokens = num_tokens_from_string(v)
                
                # If adding this segment would exceed limit, stop
                if current_tokens + v_tokens > max_tokens:
                    # If no segments added yet, return original previous_content
                    if not final_items:
                        app_logger.info(f"Cannot fit any paragraph within token limit")
                        return previous_content
                    break
                
                # Add this segment and update token count
                final_items.insert(0, item)  # Insert at front to maintain original order
                current_tokens += v_tokens
            
            # Update valid items list with segments that fit within token limit
            valid_items = final_items
        
        # Create new dictionary, keeping original keys
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
        # 使用锁保护文件访问        
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
        if not self.continue_mode:
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
            is_last_try = (retry_count == self.max_retries - 1)
            self.translated_failed = self.retranslate_failed_content(
                retry_count, 
                self.max_retries, 
                progress_callback, 
                last_try=is_last_try
            )
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
        return final_output_path, missing_counts