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
MAX_PREVIOUS_TOKENS = 256

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
            self.glossary_path
        )
        
        if not all_segments:
            app_logger.warning("No segments were generated.")
            return

        app_logger.info(f"Translating {len(all_segments)} segments...")
        

        total_segments = len(all_segments)
        for i, (segment, segment_progress, current_glossary_terms) in enumerate(all_segments):
            try:
                current_progress = (i / total_segments) if total_segments > 0 else 0
                if progress_callback:
                    progress_callback(current_progress, desc=f"Translating segment {i+1}/{total_segments}...")
                    app_logger.info(f"Progress: {current_progress * 100:.2f}%")
            
                translated_text = translate_text(
                    segment, PREVIOUS_CONTENT, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt, current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue

                translation_results = process_translation_results(segment, translated_text, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)
                PREVIOUS_CONTENT = self._update_previous_content(translation_results, PREVIOUS_CONTENT, MAX_PREVIOUS_TOKENS)
            
            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Marking segment as failed.")
                self._mark_segment_as_failed(segment)

            # 更新进度
            if progress_callback:
                progress_callback(segment_progress, desc="Translating...Please wait.")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")

    def retranslate_failed_content(self, progress_callback):
        app_logger.info("Retrying translation for failed segments (single attempt only)...")
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
            "models\Glossary.csv"
        )
        
        if not all_failed_segments:
            app_logger.info("All text has been translated.")
            return False

        # 读取原始失败段
        with open(self.failed_json_path, 'r', encoding='utf-8') as f:
            original_segments = json.load(f)
        with open(self.failed_json_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
        
        # 跟踪我们要在这次运行中处理的段
        segments_to_process = original_segments.copy()
        
        # 跟踪已翻译的内容作为上下文
        previous_content = self.previous_text_default

        # 处理所有失败段
        total_segments = len(all_failed_segments)
        for i, (segment, segment_progress, current_glossary_terms) in enumerate(all_failed_segments):
            try:
                # 显示当前进度
                current_progress = (i / total_segments) if total_segments > 0 else 0
                if progress_callback:
                    progress_callback(current_progress, desc=f"Retranslating segment {i+1}/{total_segments}...")
                    app_logger.info(f"Progress: {current_progress * 100:.2f}%")
                
                # 执行翻译，使用上一段的内容作为上下文
                translated_text = translate_text(
                    segment,
                    previous_content,
                    self.model,
                    self.use_online,
                    self.api_key,
                    self.system_prompt,
                    self.user_prompt,
                    self.previous_prompt,
                    self.glossary_prompt,
                    current_glossary_terms
                )

                if not translated_text:
                    app_logger.warning("translate_text returned empty or None.")
                    self._mark_segment_as_failed(segment)
                    continue
                
                # 处理翻译结果
                process_translation_results(segment, translated_text, self.result_split_json_path, self.failed_json_path, self.src_lang, self.dst_lang)     
                # 移除我们已处理的段
                if segment in segments_to_process:
                    segments_to_process.remove(segment)

            except (json.JSONDecodeError, ValueError, RuntimeError) as e:
                app_logger.warning(f"Error encountered: {e}. Segment will remain in failed list.")

            # 更新进度
            if progress_callback:
                progress_callback(segment_progress, desc="Missing detected! Translating once...")
                app_logger.info(f"Progress: {segment_progress * 100:.2f}%")
        
        return True

    def _update_previous_content(self, translated_text, previous_content, max_tokens):
        """
        更新前文上下文，保持最多三段翻译内容，且总token数不超过max_tokens
        
        参数:
        translated_text (str): 新翻译的文本
        previous_content (str): 当前的上下文内容
        max_tokens (int): 允许的最大token数量，默认为256
        
        返回:
        str: 更新后的上下文内容
        """
        # 检查输入的有效性
        if not translated_text or len(translated_text) <= 1:
            return previous_content
        
        # 首先检查新文本本身的token数量
        new_text_tokens = num_tokens_from_string(translated_text)
        if new_text_tokens > max_tokens:
            app_logger.info(f"New translation too long: {new_text_tokens} tokens > {max_tokens}")
            return previous_content
        
        # 如果previous_content为空，直接设置
        if not previous_content:
            app_logger.info(f"Initialized previous content: {len(translated_text)} chars, {new_text_tokens} tokens")
            return translated_text
        
        # 将previous_content分段
        paragraphs = previous_content.split('\n\n')
        
        # 添加新段落
        paragraphs.append(translated_text)
        
        # 只保留最后三段
        if len(paragraphs) > 3:
            paragraphs = paragraphs[-3:]
        
        # 从最新到最旧逐段检查token数量
        # 从最新的段落开始，尽可能多地包含旧段落
        final_paragraphs = []
        total_tokens = 0
        
        # 从最新到最旧的顺序处理
        for p in reversed(paragraphs):
            p_tokens = num_tokens_from_string(p)
            
            # 如果加上这段后超出限制，停止添加
            if total_tokens + p_tokens > max_tokens:
                break
            
            # 否则添加这段并更新token计数
            final_paragraphs.insert(0, p)  # 在前面插入，保持原来的顺序
            total_tokens += p_tokens
        
        # 如果没有能够保留的段落，说明单段就超出了限制，返回原始内容
        if not final_paragraphs:
            app_logger.info(f"No paragraphs can fit within token limit of {max_tokens}")
            return previous_content
        
        # 更新上下文内容
        updated_content = '\n\n'.join(final_paragraphs)
        app_logger.info(f"Updated previous content: {len(updated_content)} chars, {total_tokens} tokens, {len(final_paragraphs)} paragraphs")
        return updated_content

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