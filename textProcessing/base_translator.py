import os
import shutil
import json
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
PREVIOUS_CONTENT = ""
MAX_PREVIOUS_TOKENS = 128

class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, continue_mode, max_token, max_retries):
        global PREVIOUS_CONTENT
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
        PREVIOUS_CONTENT = self.previous_text_default

    def extract_content_to_json(self):
        """Abstract method: Extract document content to JSON."""
        raise NotImplementedError

    def write_translated_json_to_file(self, json_path, translated_json_path):
        """Abstract method: Write the translated JSON content back to the file."""
        raise NotImplementedError

    def translate_content(self, progress_callback):
        global PREVIOUS_CONTENT, MAX_PREVIOUS_TOKENS
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

        app_logger.info(f"Translating {len(all_segments)} segments...")
    
        for i, (segment, segment_progress, current_glossary_terms) in enumerate(all_segments):
            try:
                if progress_callback:
                    progress_callback(segment_progress, desc=f"Translating...")
            
                translated_text = translate_text(
                    segment, PREVIOUS_CONTENT, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue

                translation_results = process_translation_results(segment, translated_text, self.src_split_json_path, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)
                PREVIOUS_CONTENT = self._update_previous_content(translation_results, PREVIOUS_CONTENT, MAX_PREVIOUS_TOKENS)
            
            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                self._mark_segment_as_failed(segment)

            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")

    def retranslate_failed_content(self, retry_count, max_retries, progress_callback, last_try=False):
        global PREVIOUS_CONTENT, MAX_PREVIOUS_TOKENS
        app_logger.info(f"Retrying translation...{retry_count}/{max_retries}")
        
        if not os.path.exists(self.failed_json_path):
            app_logger.info("No failed segments to retranslate. Skipping this step.")
            return False

        # 检查文件是否为空或包含空JSON数组
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not data:  # 如果JSON是空列表或字典
                    app_logger.info("No failed segments to retranslate. Skipping this step.")
                    return False
            except json.JSONDecodeError:
                app_logger.error("Failed to decode JSON. Skipping this step.")
                return False

        # 获取所有失败段
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

        # 清空失败文件，准备重新添加新的失败段
        with open(self.failed_json_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        
        app_logger.info(f"Retranslating {len(all_failed_segments)} failed segments...")
        
        # 如果是最后一次尝试且有失败段，则逐行处理
        if last_try and all_failed_segments:
            app_logger.info("Last try mode: processing each line individually for better success rate")
            
            # 逐行处理的函数
            processed_segments = []
            for segment, segment_progress, current_glossary_terms in all_failed_segments:
                try:
                    # 解析段落中的JSON内容
                    segment_content = clean_json(segment)
                    segment_json = json.loads(segment_content)
                    
                    # 对每一行分别处理
                    for key, value in segment_json.items():
                        # 创建单行JSON
                        single_line_json = {key: value}
                        single_line_segment = f"```json\n{json.dumps(single_line_json, ensure_ascii=False, indent=4)}\n```"
                        
                        # 计算每行的进度
                        line_progress = (float(key) / max([int(k) for k in segment_json.keys() if k.isdigit()], default=1))
                        
                        # 确定该行相关的术语
                        line_glossary_terms = []
                        if current_glossary_terms:
                            line_glossary_terms = [term for term in current_glossary_terms if term[0] in value]
                        
                        # 添加到处理队列
                        processed_segments.append((single_line_segment, line_progress, line_glossary_terms))
                        
                except (json.JSONDecodeError, ValueError) as e:
                    app_logger.warning(f"Error parsing segment content: {e}. Keeping original segment.")
                    processed_segments.append((segment, segment_progress, current_glossary_terms))
            
            # 更新处理队列
            if processed_segments:
                all_failed_segments = processed_segments
                app_logger.info(f"Split into {len(all_failed_segments)} individual lines for processing")
        
        for i, (segment, segment_progress, current_glossary_terms) in enumerate(all_failed_segments):
            try:
                if progress_callback:
                    progress_callback(segment_progress, desc=f"Retranslating...")
            
                translated_text = translate_text(
                    segment, PREVIOUS_CONTENT, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue

                translation_results = process_translation_results(segment, translated_text, self.src_split_json_path, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)
                PREVIOUS_CONTENT = self._update_previous_content(translation_results, PREVIOUS_CONTENT, MAX_PREVIOUS_TOKENS)
            
            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                self._mark_segment_as_failed(segment)

            if progress_callback:
                progress_callback(segment_progress, desc="Retranslating...Please wait.")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")
        
        return True

    def _update_previous_content(self, translated_text_dict, previous_content, max_tokens):
        """
        更新前文上下文，处理translated_text_dict，保持最多三段翻译内容，且总token数不超过max_tokens
        
        参数:
        translated_text_dict (dict): 新翻译的文本，格式为字典，如{"0": "段落1", "1": "段落2"}
        previous_content (dict): 当前的上下文内容，与translated_text_dict格式相同
        max_tokens (int): 允许的最大token数量
        
        返回:
        dict: 更新后的上下文内容字典，如果translated_text_dict处理后超过token限制，则返回previous_content
        """
        # 检查输入的有效性
        if not translated_text_dict:
            return previous_content
        
        # 将字典项按键排序并转换为列表，每项为(key, value)
        sorted_items = sorted(translated_text_dict.items(), key=lambda x: x[0])
        
        # 过滤掉空值或无效值
        valid_items = [(k, v) for k, v in sorted_items if v and len(v.strip()) > 1]
        
        # 如果没有有效项，返回原内容
        if not valid_items:
            return previous_content
        
        # 只保留最后三段（最新的三段）
        if len(valid_items) > 3:
            valid_items = valid_items[-3:]
        
        # 计算所有段落的总token数
        total_tokens = sum(num_tokens_from_string(v) for _, v in valid_items)
        
        # 如果总token数超过限制，且只有一段，则返回原来的previous_content
        if total_tokens > max_tokens and len(valid_items) == 1:
            app_logger.info(f"Single paragraph exceeds token limit: {total_tokens} tokens > {max_tokens}")
            return previous_content
        
        # 如果总token数超过限制，需要裁剪段落
        if total_tokens > max_tokens:
            # 从最新到最旧尝试保留段落
            final_items = []
            current_tokens = 0
            
            # 从最新段落开始添加
            for item in reversed(valid_items):
                k, v = item
                v_tokens = num_tokens_from_string(v)
                
                # 如果添加这段后会超过限制，停止添加
                if current_tokens + v_tokens > max_tokens:
                    # 如果还没有添加任何段落，返回原来的previous_content
                    if not final_items:
                        app_logger.info(f"Cannot fit any paragraph within token limit")
                        return previous_content
                    break
                
                # 添加这段并更新token计数
                final_items.insert(0, item)  # 在前面插入，保持原来的顺序
                current_tokens += v_tokens
            
            # 更新有效项列表为可以符合token限制的项
            valid_items = final_items
        
        # 创建新的字典，保留原始键
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
            self.translated_failed = self.retranslate_failed_content(retry_count, self.max_retries, progress_callback)    
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