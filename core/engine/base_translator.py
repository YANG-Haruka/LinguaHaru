import os
import shutil
import json
import time
import uuid
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import RLock
from core.log_config import app_logger, file_logger
from .calculation_tokens import num_tokens_from_string
from core.translation_history import TranslationHistoryManager, create_translation_record

from core.llm.llm_wrapper import translate_text
from core.llm.online_translation import HardApiError
from core.engine.text_separator import (
    stream_segment_json, split_text_by_token_limit,
    deduplicate_translation_content, create_deduped_json_for_translation,
    restore_translations_from_deduped
)
from core.load_prompt import load_prompt
from core.languages_config import LABEL_TRANSLATIONS
from .translation_checker import (
    process_translation_results, clean_json, check_and_sort_translations,
    flush_results, invalidate_results)

# File path constants
SRC_JSON_PATH = "src.json"
SRC_DEDUPED_JSON_PATH = "src_deduped.json"
SRC_SPLIT_JSON_PATH = "src_deduped_split.json"
RESULT_SPLIT_JSON_PATH = "dst_translated_split.json"
FAILED_JSON_PATH = "dst_translated_failed.json"
NEEDS_REVIEW_JSON_PATH = "dst_needs_review.json"
RESULT_JSON_PATH = "dst_translated.json"
MAX_PREVIOUS_TOKENS = 128

class DocumentTranslator:
    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang, continue_mode, max_token, max_retries, thread_count, glossary_path, temp_dir, result_dir, session_lang="en", log_dir="log", history_dir=None, batch_id=None, batch_size=None):
        self.input_file_path = input_file_path
        # Batch grouping: files from one run share batch_id; batch_size = N files.
        self.batch_id = batch_id
        self.batch_size = batch_size
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
        self.lock = RLock()
        self.check_stop_requested = None
        self.last_ui_update_time = 0
        self.temp_dir = temp_dir
        self.result_dir = result_dir
        self.session_lang = session_lang
        self.log_dir = log_dir
        # Where the translation-history DB lives; defaults to log_dir. The web
        # frontend overrides this so local single-user mode shares ONE history
        # with the Qt desktop app, while LAN/server mode stays per-session.
        self.history_dir = history_dir or log_dir

        # Translation history tracking
        self.translation_id = str(uuid.uuid4())
        self.translation_start_time = None
        self.translation_end_time = None

        # Token usage tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0

        # Final run stats (segments / speed / tokens), filled at the end of process()
        self.final_stats = ""

        # Cumulative progress: successfully translated segments vs. the total.
        # Counted across the first pass AND every retry so progress only moves
        # forward (failed segments don't count, retries don't reset to 0%).
        self._total_segments = 0
        self._completed_segments = 0

        # Setup file paths
        filename = os.path.splitext(os.path.basename(input_file_path))[0]
        self.file_dir = os.path.join(self.temp_dir, filename)
        
        # File paths
        self.src_json_path = os.path.join(self.file_dir, SRC_JSON_PATH)
        self.src_deduped_json_path = os.path.join(self.file_dir, SRC_DEDUPED_JSON_PATH)
        self.src_split_json_path = os.path.join(self.file_dir, SRC_SPLIT_JSON_PATH)
        self.result_split_json_path = os.path.join(self.file_dir, RESULT_SPLIT_JSON_PATH)
        self.failed_json_path = os.path.join(self.file_dir, FAILED_JSON_PATH)
        self.needs_review_json_path = os.path.join(self.file_dir, NEEDS_REVIEW_JSON_PATH)
        self.result_json_path = os.path.join(self.file_dir, RESULT_JSON_PATH)
        
        # Deduplication mapping
        self.count_src_to_deduped_map = None
        
        os.makedirs(self.file_dir, exist_ok=True)

        # Load prompts
        self.system_prompt, self.user_prompt, self.previous_prompt, self.previous_text_default, self.glossary_prompt = load_prompt(src_lang, dst_lang)
        # Start with NO prior context. The old prompts seeded this with example
        # sentences ("Hello / This is an automatic translation system…"), which
        # polluted the first batch — especially short strings, titles and button
        # labels. Real context accumulates as segments translate (single-threaded;
        # see the ordering note in translate()).
        self.previous_content = {}

        # Snapshot the translation mode/options ONCE so a concurrent task or
        # another LAN user changing the global config mid-run can't perturb this
        # task's sampling / second pass / QA / context behavior.
        try:
            from core import translation_modes
            self.topts = translation_modes.snapshot()
        except Exception:  # noqa: BLE001
            self.topts = {"params": {}, "with_context": False}

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
        from core.languages_config import LANGUAGE_MAP
        for display_name, code in LANGUAGE_MAP.items():
            if code == lang_code:
                return display_name
        # If not found, return the code itself
        return lang_code

    def _log_progress(self, fraction):
        """Throttled progress for the project log: at most every 5% or 30s (plus
        100%). The UI already shows live progress; this keeps the log readable."""
        import time
        now = time.time()
        if (fraction - getattr(self, "_last_log_frac", -1.0) >= 0.05
                or now - getattr(self, "_last_log_time", 0.0) >= 30
                or fraction >= 1.0):
            app_logger.info(f"Progress: {fraction:.0%}")
            self._last_log_frac = fraction
            self._last_log_time = now

    def _get_current_log_file_path(self):
        """This run's per-project log file (in the result folder), set by
        process() when it opened the task log."""
        path = getattr(self, "_task_log_path", None)
        if path:
            return path
        # Fallback: construct a reasonable path
        input_filename = os.path.basename(self.input_file_path)
        return os.path.join(self.result_dir, f"{input_filename}.log")

    def _save_translation_summary(self, status, output_file_path=None,
                                  error_reason=None, error_category=None):
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

            # Approximate cost for the project record (online only).
            cost_amount = cost_currency = None
            if self.use_online and self.total_tokens > 0:
                try:
                    from core.pricing import estimate_cost
                    cost_amount, _symbol, cost_currency = estimate_cost(
                        self.model, self.total_prompt_tokens,
                        self.total_completion_tokens, self.session_lang)
                    cost_amount = round(cost_amount, 4)
                except Exception:  # noqa: BLE001
                    cost_amount = cost_currency = None

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
                status=status,
                cost_amount=cost_amount,
                cost_currency=cost_currency,
                translation_options=getattr(self, "topts", None),
                error_reason=error_reason,
                error_category=error_category,
                resume_info=(self._build_resume_info()
                             if status in ("failed", "stopped", "running", "interrupted")
                             else None),
                batch_id=self.batch_id,
                batch_size=self.batch_size,
            )

            # Save to history
            history_manager = TranslationHistoryManager(log_dir=self.history_dir)
            history_manager.add_record(record)

            app_logger.info(f"Translation summary saved: {status}, tokens: {self.total_tokens}")
        except Exception as e:
            app_logger.error(f"Error saving translation summary: {e}")

    def _build_resume_info(self):
        """Params needed to re-run THIS file in continue_mode. A frontend may
        pre-set ``self.resume_info`` (e.g. bilingual flags / glossary name it
        knows but the translator doesn't); those values win, and we fill in the
        core params every resume needs."""
        info = dict(getattr(self, "resume_info", {}) or {})
        info.setdefault("input_file_path", self.input_file_path)
        info.setdefault("src_lang", self.src_lang)
        info.setdefault("dst_lang", self.dst_lang)
        info.setdefault("model", self.model)
        info.setdefault("use_online", self.use_online)
        info.setdefault("temp_dir", self.temp_dir)
        info.setdefault("result_dir", self.result_dir)
        info.setdefault("log_dir", self.log_dir)
        info.setdefault("max_token", self.max_token)
        info.setdefault("max_retries", self.max_retries)
        info.setdefault("thread_count", self.num_threads)
        return info

    def save_failed_summary(self, error_reason=None, error_category=None):
        """Save translation summary with failed status - called on error"""
        # Persist whatever was translated so far so the run is resumable.
        flush_results(self.result_split_json_path)
        if self.translation_start_time:
            self._save_translation_summary(
                status="failed", error_reason=error_reason, error_category=error_category)

    def save_stopped_summary(self):
        """Save translation summary with stopped status - called when user stops"""
        flush_results(self.result_split_json_path)
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

    def update_ui_safely(self, progress_callback, progress, desc, force=False):
        """Update UI, throttled to ~10/s — but ALWAYS emit terminal/forced
        updates (progress>=1.0), so the final 100% + summary never gets swallowed
        by the throttle on a fast run."""
        self.check_for_stop()

        current_time = time.time()
        if force or progress >= 1.0 or current_time - self.last_ui_update_time >= 0.1:
            try:
                if progress_callback:
                    # The live stats line + final summary already carry the token
                    # count, so don't double-append it here (it duplicated, and
                    # broke for non-English UIs whose label isn't "tokens").
                    progress_callback(progress, desc=desc)
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
                        "Continuing translation..."
                    )
                
            except Exception as e:
                app_logger.warning(f"Could not determine previous progress: {str(e)}")
                total_segments = total_current_batch
                remaining_ratio = 1.0
        else:
            total_segments = total_current_batch
        
        def process_segment(segment_data):
            """Translate one batch ONCE (translate_text already does bounded
            transport retries). On any failure the batch's items are marked failed
            and picked up by the outer retry rounds (retranslate_failed_content) —
            no in-place 1-hour loop. HardApiError (bad key/quota) aborts the task."""
            segment, segment_progress, current_glossary_terms = segment_data[:3]
            context_map = segment_data[3] if len(segment_data) > 3 else None
            self.check_for_stop()

            # Running "previous content" is only safe single-threaded (ordered);
            # under concurrency completion order is nondeterministic, so disable it.
            if self.num_threads > 1:
                current_previous = ""
            else:
                with self.lock:
                    current_previous = self.previous_content

            try:
                translated_text, success, token_usage = translate_text(
                    segment, current_previous, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt,
                    current_glossary_terms, check_stop_callback=self.check_for_stop,
                    context_map=context_map, options=self.topts
                )
                self._add_token_usage(token_usage)

                if not success or not translated_text:
                    self._mark_segment_as_failed(segment)
                    return None

                # Optional second pass ("polish" mode): safe — keeps the first pass
                # if the polish output isn't valid same-key JSON.
                try:
                    if self.topts.get("params", {}).get("second_pass"):
                        from core.llm.llm_wrapper import polish_translation
                        translated_text, _polish_usage = polish_translation(
                            translated_text, self.dst_lang, self.model,
                            self.use_online, self.api_key,
                            check_stop=self.check_for_stop, options=self.topts)
                        self._add_token_usage(_polish_usage)
                except Exception as e:  # noqa: BLE001 — never break on polish
                    app_logger.warning(f"Second pass skipped: {e}")

                # Validate + commit. process_translation_results does PARTIAL
                # acceptance: valid items are written, the rest go to failed.json.
                with self.lock:
                    translation_results = process_translation_results(
                        segment, translated_text,
                        self.src_split_json_path, self.result_split_json_path, self.failed_json_path,
                        self.src_lang, self.dst_lang
                    )
                    if translation_results:
                        if self.num_threads == 1:
                            self.previous_content = self._update_previous_content(
                                translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                            )
                        return translation_results
                    self._mark_segment_as_failed(segment)
                    return None
            except HardApiError:
                raise   # bad key/quota -> abort the whole task
            except Exception as e:  # noqa: BLE001
                app_logger.warning(f"Error processing segment (deferring to retry rounds): {e}")
                self._mark_segment_as_failed(segment)
                return None

        # Translate segments in parallel
        with ThreadPoolExecutor(
                max_workers=self.num_threads,
                initializer=file_logger.worker_initializer,
                initargs=(self.translation_id,)) as executor:
            futures = []
            for seg in all_segments:
                future = executor.submit(process_segment, seg)
                futures.append(future)
            
            if not self.continue_mode:
                self.update_ui_safely(progress_callback, 0.0, f"{self._get_status_message('Translating')}...")

            current_batch_completed = 0
            failed_segments_count = 0
            batch_start_time = time.time()

            for future in as_completed(futures):
                ok = False
                result = None
                try:
                    result = future.result()
                    ok = result is not None
                    if not ok:
                        failed_segments_count += 1
                except HardApiError:
                    # Cancel what hasn't started and abort the task
                    for pending in futures:
                        pending.cancel()
                    raise
                except Exception as e:
                    failed_segments_count += 1
                    app_logger.error(f"Segment translation error: {e}")

                if ok:
                    # Count translated ITEMS, not batches: a segment packs many
                    # source lines, so +=1 per batch made the bar stall far below
                    # 100%. result is the per-item {count_split: text} dict.
                    with self.lock:
                        self._completed_segments += len(result)
                current_batch_completed += 1
                stats_desc = self._build_stats_desc(
                    f"{self._get_status_message('Translating')}...",
                    current_batch_completed, total_current_batch,
                    batch_start_time, failed_segments_count
                )

                # Cumulative progress: completed segments / total (never resets)
                overall_progress = min(self._completed_segments / max(self._total_segments, 1), 1.0)
                self._log_progress(overall_progress)
                self.update_ui_safely(progress_callback, overall_progress, stats_desc)

    def retranslate_failed_content(self, retry_count, max_retries, progress_callback,
                                   last_try=False, max_token=None):
        self.check_for_stop()
        batch_token = max_token or self.max_token   # geometric-shrink budget per round
        app_logger.info(f"Retrying failed translations...{retry_count}/{max_retries} "
                        f"(batch≈{batch_token} tok)")
        
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

        # Get failed segments (re-chunked at this round's shrunk batch budget)
        all_failed_segments = stream_segment_json(
            self.failed_json_path,
            batch_token,
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
            
            # Count total lines (segments are 4-tuples: output, progress,
            # glossary_terms, segment_types — tolerate the trailing element)
            for segment, _, _, *_ in all_failed_segments:
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
            for segment, segment_progress, current_glossary_terms, *_ in all_failed_segments:
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
            """Re-translate one failed batch ONCE (translate_text does bounded
            transport retries). Still-failing items stay failed for the next
            round; no in-place 1-hour loop. HardApiError aborts the task."""
            segment, segment_progress, current_glossary_terms = segment_data[:3]
            context_map = segment_data[3] if len(segment_data) > 3 else None
            self.check_for_stop()

            if self.num_threads > 1:
                current_previous = ""
            else:
                with self.lock:
                    current_previous = self.previous_content

            try:
                translated_text, success, token_usage = translate_text(
                    segment, current_previous, self.model, self.use_online, self.api_key,
                    self.system_prompt, self.user_prompt, self.previous_prompt, self.glossary_prompt,
                    current_glossary_terms, check_stop_callback=self.check_for_stop,
                    context_map=context_map, options=self.topts
                )
                self._add_token_usage(token_usage)

                if not success or not translated_text:
                    self._mark_segment_as_failed(segment)
                    return None

                try:
                    if self.topts.get("params", {}).get("second_pass"):
                        from core.llm.llm_wrapper import polish_translation
                        translated_text, _polish_usage = polish_translation(
                            translated_text, self.dst_lang, self.model,
                            self.use_online, self.api_key,
                            check_stop=self.check_for_stop, options=self.topts)
                        self._add_token_usage(_polish_usage)
                except Exception as e:  # noqa: BLE001 — never break on polish
                    app_logger.warning(f"Second pass skipped: {e}")

                with self.lock:
                    translation_results = process_translation_results(
                        segment, translated_text,
                        self.src_split_json_path, self.result_split_json_path,
                        self.failed_json_path, self.src_lang, self.dst_lang,
                        last_try=last_try, needs_review_path=self.needs_review_json_path
                    )
                    if translation_results:
                        if self.num_threads == 1:
                            self.previous_content = self._update_previous_content(
                                translation_results, self.previous_content, MAX_PREVIOUS_TOKENS
                            )
                        return translation_results
                    self._mark_segment_as_failed(segment)
                    return None
            except HardApiError:
                raise
            except Exception as e:  # noqa: BLE001
                app_logger.warning(f"Error processing failed segment (deferring): {e}")
                self._mark_segment_as_failed(segment)
                return None

        # Process failed segments in parallel
        with ThreadPoolExecutor(
                max_workers=self.num_threads,
                initializer=file_logger.worker_initializer,
                initargs=(self.translation_id,)) as executor:
            futures = []
            for seg in all_failed_segments:
                future = executor.submit(process_failed_segment, seg, last_try)
                futures.append(future)
            
            self.update_ui_safely(progress_callback, 0.0, f"{retry_desc}...")
            
            completed = 0
            failed_count = 0
            
            for future in as_completed(futures):
                ok = False
                try:
                    result = future.result()
                    ok = result is not None
                    if not ok:
                        failed_count += 1
                        app_logger.debug("Segment processing failed")
                except HardApiError:
                    for pending in futures:
                        pending.cancel()
                    raise
                except Exception as e:
                    failed_count += 1
                    app_logger.error(f"Failed segment error: {e}")

                if ok:
                    with self.lock:
                        self._completed_segments += len(result)
                completed += 1
                # Cumulative progress: keep counting up from where we were, so a
                # retry pass continues (e.g. 90% -> 100%) instead of restarting.
                overall = min(self._completed_segments / max(self._total_segments, 1), 1.0)
                self._log_progress(overall)
                # Show the live per-pass count too (done/total of THIS retry
                # pass), so the line visibly ticks even though the global bar is
                # already near 100% during the failed-segment cleanup.
                self.update_ui_safely(
                    progress_callback,
                    overall,
                    f"{retry_desc} {retry_count+1}/{max_retries} · {completed}/{total}"
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
                        app_logger.info("Cannot fit any paragraph within token limit")
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
    
    def _maybe_extract_glossary(self, deduped_data, progress_callback=None):
        """If enabled in system config, AI-extract terms from the document and
        merge them with the user glossary for this run (user terms win)."""
        try:
            from core.paths import SYSTEM_CONFIG
            with open(SYSTEM_CONFIG, encoding="utf-8") as f:
                enabled = bool(json.load(f).get("auto_extract_glossary", False))
        except Exception:
            enabled = False
        if not enabled:
            return
        self.check_for_stop()   # honor a pending Stop before the extraction call
        try:
            from core.engine.glossary_extractor import (
                extract_glossary_terms, write_merged_glossary)
            from core.engine.text_separator import load_glossary

            self.update_ui_safely(progress_callback, 0,
                                  f"{self._get_status_message('Extracting glossary')}...")
            values = []
            for item in (deduped_data or []):
                value = item.get("value") if isinstance(item, dict) else (
                    item if isinstance(item, str) else None)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())

            terms = extract_glossary_terms(values, self.model, self.use_online,
                                           self.api_key, self.src_lang, self.dst_lang,
                                           check_stop=self.check_for_stop,
                                           mode_params=self.topts.get("params"))
            if not terms:
                return

            user_entries = []
            if self.glossary_path and os.path.exists(self.glossary_path):
                user_entries = load_glossary(self.glossary_path, self.src_lang, self.dst_lang)

            merged_path = os.path.join(self.file_dir, "auto_glossary.csv")
            write_merged_glossary(terms, user_entries, merged_path,
                                  self.src_lang, self.dst_lang)
            self.glossary_path = merged_path

            # Review copy next to the translation output
            review_name = (os.path.splitext(os.path.basename(self.input_file_path))[0]
                           + "_glossary.csv")
            try:
                shutil.copyfile(merged_path, os.path.join(self.result_dir, review_name))
            except OSError:
                pass
            app_logger.info(f"Using merged glossary with {len(terms)} AI-extracted terms")
        except Exception as e:
            app_logger.warning(f"AI glossary extraction skipped due to error: {e}")

    def _apply_text_rules(self, json_file_path, phase):
        """Apply user replacement rules to a translation JSON file in place.

        phase "replace_before" rewrites the "value" field (text sent to the
        LLM); "replace_after" rewrites the "translated" field."""
        from core.text_rules import load_rules, apply_replace_before, apply_replace_after
        rules = load_rules()
        if not rules.get(phase):
            return
        try:
            with open(json_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            apply = apply_replace_before if phase == "replace_before" else apply_replace_after
            field = "value" if phase == "replace_before" else "translated"
            changed = 0
            for item in data:
                if isinstance(item, dict) and isinstance(item.get(field), str):
                    replaced = apply(item[field])
                    if replaced != item[field]:
                        item[field] = replaced
                        changed += 1
            if changed:
                with open(json_file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                app_logger.info(f"Applied {phase} rules to {changed} items")
        except Exception as e:
            app_logger.warning(f"Failed to apply {phase} rules: {e}")

    def _build_stats_desc(self, base_desc, completed, total, batch_start_time, failed_count):
        """Live stats line for the progress bar, all at the ITEM level: items
        done/total, speed (lines/min), ETA, thread count, tokens, failures."""
        try:
            with self.lock:
                done = self._completed_segments
                total_items = max(self._total_segments, 1)
                tokens = self.total_tokens
            start = getattr(self, "_run_start", None) or batch_start_time
            elapsed = max(time.time() - start, 0.001)
            rate_per_min = done / elapsed * 60
            remaining_s = (total_items - done) / (done / elapsed) if done else 0
            eta = f"{int(remaining_s // 60)}:{int(remaining_s % 60):02d}"
            desc = (f"{base_desc} {done}/{total_items}"
                    f" | {rate_per_min:.0f} {self._get_status_message('lines/min')}"
                    f" | ETA {eta}"
                    f" | {self.num_threads} {self._get_status_message('threads')}"
                    f" | {self._format_tokens(tokens)} tokens")
            if failed_count:
                desc += f" | failed {failed_count}"
            return desc
        except Exception:
            return base_desc

    def _write_manifest(self, file_extension):
        """Persist what the Proofread tab needs to re-export this document:
        a copy of the original input file plus a small manifest.json inside
        self.file_dir. Best-effort and non-invasive: failures only log."""
        try:
            ext = file_extension.lower()
            os.makedirs(self.file_dir, exist_ok=True)
            # Keep the document's own base name: writers locate their
            # intermediates (e.g. all_content.json) via temp_dir/<input name>,
            # so the copy must resolve to this same folder on re-export.
            base_name = os.path.splitext(os.path.basename(self.input_file_path))[0]
            original_copy = f"{base_name}{ext}"
            copy_path = os.path.join(self.file_dir, original_copy)
            if not os.path.exists(copy_path) and os.path.exists(self.input_file_path):
                shutil.copyfile(self.input_file_path, copy_path)
            manifest = {
                "input_file": os.path.basename(self.input_file_path),
                "original_copy": original_copy,
                "file_extension": ext,
                "src_lang": self.src_lang,
                "dst_lang": self.dst_lang,
                "model": self.model,
                "use_online": self.use_online,
                "bilingual_mode": bool(getattr(self, "bilingual_mode", False)),
                "use_xlwings": bool(getattr(self, "use_xlwings", False)),
            }
            with open(os.path.join(self.file_dir, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            app_logger.warning(f"Could not write proofread manifest: {e}")

    def _clear_temp_folder(self):
        """Clear THIS file's working directory.

        Scoped to self.file_dir (temp_dir/<filename>), NOT the shared temp_dir:
        several files may translate concurrently under the same temp_dir, so
        wiping the whole root would delete a sibling task's in-flight data
        (FileNotFoundError mid-run). Only our own working dir is reset."""
        folder = self.file_dir
        try:
            if os.path.exists(folder):
                app_logger.info("Clearing working folder...")
                shutil.rmtree(folder)
        except Exception as e:
            app_logger.warning(f"Could not delete working folder: {str(e)}")
        finally:
            os.makedirs(folder, exist_ok=True)
    
    def _mark_segment_as_failed(self, segment):
        """Mark segment as failed. Thread-safe: self.lock is an RLock, so this
        is safe to call both inside and outside `with self.lock:` blocks."""
        app_logger.debug("Marking segment as failed")

        with self.lock:
            self._mark_segment_as_failed_locked(segment)

    def _mark_segment_as_failed_locked(self, segment):
        # Ensure file exists
        if not os.path.exists(self.failed_json_path):
            try:
                with open(self.failed_json_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
                app_logger.debug("Created failed segments file")
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
                        "value": str(value).strip()    # value may be non-str on a malformed reply
                    })
                    
                f.seek(0)
                f.truncate()
                json.dump(failed_segments, f, ensure_ascii=False, indent=4)
                app_logger.debug(f"Saved {len(segment_dict)} items to failed list")
                
        except Exception as e:
            app_logger.error(f"Error updating failed segments: {e}")
    
    def process(self, file_name, file_extension, progress_callback=None):
        """Main processing method.

        Thin wrapper around _process_impl that records the outcome in history
        even when the run does NOT finish: a fatal API error (HardApiError) or
        any unexpected exception -> a "failed" record (with the reason), a user
        stop -> a "stopped" record. Both keep enough info to resume later. The
        exception is always re-raised so callers still see the failure.

        This is also where the per-project log is started: an isolated log file in
        the result folder, bound to THIS task's context so its (and its worker
        threads') logs route there even when several files translate at once."""
        from core import log_config
        flog = log_config.file_logger
        base = os.path.basename(self.input_file_path)
        self._task_log_path = flog.open_task_log(self.translation_id, self.result_dir, base)
        token = flog.bind_task(self.translation_id)
        # One standardized run-start line (project + system log) — no source text.
        log_config.system_event(
            f"Run start: {base} [{file_extension}] {self.src_lang}->{self.dst_lang} | "
            f"{self.model} ({'online' if self.use_online else 'offline'}) | "
            f"threads={self.num_threads} max_token={self.max_token} | out={self.result_dir}")
        start_t = time.time()
        try:
            result = self._process_impl(file_name, file_extension, progress_callback)
            out = result[0] if isinstance(result, (tuple, list)) else result
            missing = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else None
            log_config.system_event(
                f"Run finish: {base} | success | {int(time.time() - start_t)}s | "
                f"tokens={self.total_tokens} | missing={len(missing or [])} | "
                f"out={os.path.basename(str(out))}")
            return result
        except HardApiError as e:
            category = getattr(e, "category", "api_error")
            log_config.system_event(
                f"Run finish: {base} | aborted [{category}] | {int(time.time()-start_t)}s | "
                f"tokens={self.total_tokens}", level=logging.ERROR)
            self.save_failed_summary(error_reason=str(e), error_category=category)
            raise
        except BaseException as e:  # noqa: BLE001 — record, then re-raise
            # Frontend stop exceptions propagate through here; match by name or
            # the Web stop marker to avoid importing frontend modules into the
            # backend (Qt _StopRequested, Web RuntimeError("__stopped__")).
            if (type(e).__name__ in ("_StopRequested", "StopTranslationException")
                    or "__stopped__" in str(e)):
                log_config.system_event(f"Run finish: {base} | stopped by user | "
                                        f"{int(time.time()-start_t)}s")
                self.save_stopped_summary()
            else:
                log_config.system_event(
                    f"Run finish: {base} | failed | {int(time.time()-start_t)}s | {e}",
                    level=logging.ERROR)
                self.save_failed_summary(error_reason=str(e))
            raise
        finally:
            flog.unbind_task(token)
            flog.close_task_log(self.translation_id)

    def _process_impl(self, file_name, file_extension, progress_callback=None):
        """Main processing method"""

        # Record translation start time
        self.translation_start_time = datetime.now()
        # Write the record up-front as "running" so even a hard crash / force-quit
        # (which kills the process before any except: handler can run) still
        # leaves a resumable entry. A startup sweep later flips orphaned "running"
        # rows to "interrupted"; a graceful finish/fail/stop overwrites this row.
        if not self.continue_mode:
            try:
                self._save_translation_summary(status="running")
            except Exception as e:  # noqa: BLE001 — never block a run on history
                app_logger.warning(f"Could not write start record: {e}")
        # A fresh run must not inherit a stale in-memory result buffer (same
        # process re-translating the same doc).
        invalidate_results(self.result_split_json_path)

        # Phased progress: when a translator declares EXTRACTION_PROGRESS_SHARE
        # (e.g. video transcription / image OCR is a big up-front step), map the
        # extraction phase into [0, share] and the translation phase into
        # [share, 1] so the bar moves during BOTH. Default 0 = no phasing (the
        # extraction is instant for plain docs, translation keeps the full bar).
        _orig_cb = progress_callback
        _share = getattr(self, "EXTRACTION_PROGRESS_SHARE", 0.0) or 0.0

        def _extract_cb(value, desc=None):
            if _orig_cb:
                _orig_cb(float(value) * _share, desc=desc)

        def _trans_cb(value, desc=None):
            if _orig_cb:
                _orig_cb(_share + float(value) * (1.0 - _share), desc=desc)

        # The rest of process() is the translation phase; extraction uses _extract_cb.
        progress_callback = _trans_cb

        # Continue mode
        if self.continue_mode:
            app_logger.info("Continue mode: checking existing files...")
            
            # Check source JSON
            if not os.path.exists(self.src_json_path):
                app_logger.info("Source JSON not found, extracting content...")
                self.extract_content_to_json(_extract_cb)
            
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
            self.extract_content_to_json(_extract_cb)

            app_logger.info("Deduplicating content...")
            self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Removing duplicates')}...")
            deduped_data, self.count_src_to_deduped_map = deduplicate_translation_content(self.src_json_path)
            create_deduped_json_for_translation(deduped_data, self.src_deduped_json_path)

            app_logger.info("Splitting content...")
            self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Splitting text')}...")
            split_text_by_token_limit(self.src_deduped_json_path)

            # User pre-translation replacement rules (config/text_rules.json),
            # applied to the text sent to the LLM; originals stay untouched
            self._apply_text_rules(self.src_split_json_path, "replace_before")

            # Optional AI glossary extraction (system_config: auto_extract_glossary)
            self._maybe_extract_glossary(deduped_data, progress_callback)

        # Persist re-export data (original copy + manifest) for the Proofread tab
        self._write_manifest(file_extension)

        # Seed cumulative-progress counters: total segments to translate, and how
        # many are already done (non-zero only in continue mode).
        try:
            with open(self.src_split_json_path, 'r', encoding='utf-8') as f:
                self._total_segments = len(json.load(f))
        except Exception:
            self._total_segments = 0
        try:
            if os.path.exists(self.result_split_json_path):
                with open(self.result_split_json_path, 'r', encoding='utf-8') as f:
                    self._completed_segments = len(json.load(f))
        except Exception:
            self._completed_segments = 0
        self._run_start = time.time()   # for item-level speed / ETA

        # Main translation
        app_logger.info("Starting translation...")
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Translating content')}...")
        self.translate_content(progress_callback)

        # Retry failed translations. Each round shrinks the batch GEOMETRICALLY
        # (halve toward a single-segment floor): a smaller batch is more likely to
        # pass and re-bills less. Only still-failed items are re-collected, so
        # passing items committed in earlier rounds are never resent.
        _MIN_BATCH_TOKEN = 256
        retry_count = 0
        while retry_count < self.max_retries and self.translated_failed:
            is_last_try = (retry_count == self.max_retries - 1)
            shrink_token = max(_MIN_BATCH_TOKEN,
                               int(self.max_token * (0.5 ** (retry_count + 1))))
            self.translated_failed = self.retranslate_failed_content(
                retry_count,
                self.max_retries,
                progress_callback,
                last_try=is_last_try,
                max_token=shrink_token,
            )
            retry_count += 1

        # Post-processing — flush the buffered results to disk first, since
        # check/sort and restore read the result file directly.
        flush_results(self.result_split_json_path)
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

        # User post-translation replacement rules
        self._apply_text_rules(self.result_json_path, "replace_after")

        # Write output
        app_logger.info("Writing output...")
        self.update_ui_safely(progress_callback, 0, f"{self._get_status_message('Generating output')}...")
        self.write_translated_json_to_file(self.src_json_path, self.result_json_path, progress_callback)

        # Final stats (segments, speed, tokens) for the completion status message
        try:
            elapsed = max((datetime.now() - self.translation_start_time).total_seconds(), 0.001)
            with open(self.src_split_json_path, 'r', encoding='utf-8') as f:
                seg_total = len(json.load(f))
            rate_per_min = seg_total / elapsed * 60
            parts = [
                f"{seg_total} {self._get_status_message('Segments')}",
                f"{rate_per_min:.1f} {self._get_status_message('lines/min')}",
                f"{self.num_threads} {self._get_status_message('threads')}",
                f"{self._get_status_message('Total tokens used')}: {self._format_tokens(self.total_tokens)}",
            ]
            # Approximate cost (online only), in the UI language's currency.
            if self.use_online and self.total_tokens > 0:
                from core.pricing import estimate_cost
                amount, symbol, ccy = estimate_cost(
                    self.model, self.total_prompt_tokens,
                    self.total_completion_tokens, self.session_lang)
                parts.append(f"{self._get_status_message('Estimated cost')}: "
                             f"{symbol}{amount:.4f} {ccy}")
            self.final_stats = " | ".join(parts)
        except Exception:
            self.final_stats = ""

        # Complete - show total tokens used
        completion_msg = self._get_status_message('Translation completed')
        tokens_msg = self._get_status_message('Total tokens used')
        if self.final_stats:
            completion_msg = f"{completion_msg} | {self.final_stats}"
        elif self.total_tokens > 0:
            completion_msg = f"{completion_msg} | {tokens_msg}: {self._format_tokens(self.total_tokens)}"
        self.update_ui_safely(progress_callback, 1.0, completion_msg)

        # Return output path
        result_folder = self.result_dir
        base_name = os.path.basename(file_name)
        # Use source_lang2target_lang format (e.g., zh2ja)
        lang_suffix = f"{self.src_lang}2{self.dst_lang}"
        # Writers emit lowercase extensions; keep the returned path consistent
        # (matters on case-sensitive filesystems when the upload was .DOCX etc.)
        final_output_path = os.path.join(result_folder, f"{base_name}_{lang_suffix}{file_extension.lower()}")

        # Translation coverage report (best-effort; must never break a run)
        self._write_coverage_report()
        # Mode-aware QA warnings (best-effort, non-blocking)
        self._write_qa_report()

        # Save translation summary
        self._save_translation_summary(status="success", output_file_path=final_output_path)

        return final_output_path, missing_counts

    def _write_coverage_report(self):
        """Compute the coverage breakdown from this run's src.json /
        dst_translated.json, log a one-line summary, and drop coverage.json into
        the result dir so a frontend can read it. Fully guarded — coverage MUST
        NEVER break a translation."""
        try:
            from core import coverage
            report = coverage.summarize(self.src_json_path, self.result_json_path,
                                        self.needs_review_json_path)
            app_logger.info("Coverage: " + coverage.format_line(report))
            os.makedirs(self.result_dir, exist_ok=True)
            out_path = os.path.join(self.result_dir, "coverage.json")
            tmp_path = out_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, out_path)
        except Exception as e:  # noqa: BLE001 — coverage is non-essential
            app_logger.warning(f"Could not write coverage report: {e}")

    def _write_qa_report(self):
        """Run the active translation mode's QA checks over this run's result and
        log + write qa.json. Fully guarded — QA is advisory and MUST NEVER break
        a translation."""
        try:
            import json as _json
            from core.engine import translation_qa
            qa_list = self.topts.get("params", {}).get("qa", [])
            if not qa_list:
                return
            with open(self.result_json_path, encoding="utf-8") as f:
                dst_items = _json.load(f)
            glossary = []
            if self.glossary_path and os.path.exists(self.glossary_path):
                from core.engine.text_separator import load_glossary
                glossary = load_glossary(self.glossary_path, self.src_lang, self.dst_lang)
            warns = translation_qa.run(qa_list, dst_items, glossary)
            if warns:
                summary = ", ".join(f"{k}: {len(v)}" for k, v in warns.items())
                app_logger.info(f"QA warnings ({summary})")
            os.makedirs(self.result_dir, exist_ok=True)
            out_path = os.path.join(self.result_dir, "qa.json")
            tmp_path = out_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                _json.dump(warns, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, out_path)
        except Exception as e:  # noqa: BLE001 — QA must never raise
            app_logger.warning(f"Could not write QA report: {e}")