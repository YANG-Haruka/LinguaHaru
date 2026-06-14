"""
PDF Translator Module - BabelDOC single-pass implementation

BabelDOC runs ONCE (parse -> layout analysis -> paragraph detection ->
typeset), calling our translator callback for every paragraph. The callback
reuses LinguaHaru's prompt stack, per-paragraph glossary matching, retry and
stop handling. Concurrency is raised from BabelDOC's default (qps=4) to the
user-selected thread count, and BabelDOC's persistent SQLite translation
cache provides dedupe plus crash resilience (re-running a document hits the
cache for already-translated paragraphs).

The previous implementation ran the FULL BabelDOC pipeline twice (an
extraction pass and a write pass), performing the expensive document layout
analysis two times per translation.
"""

import json
import os
import shutil
import threading
from pathlib import Path

from babeldoc.translator.translator import BaseTranslator
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.format.pdf.high_level import do_translate, get_translation_stage, init as babeldoc_init
from babeldoc.docvision.base_doclayout import DocLayoutModel
from babeldoc.progress_monitor import ProgressMonitor

from core.llm.llm_wrapper import translate_text
from core.engine.base_translator import DocumentTranslator
from core.engine.text_separator import load_glossary, find_terms_with_hashtable
from core.pipelines.skip_pipeline import should_translate
from core.engine.translation_checker import clean_json
from core.log_config import app_logger

# Ensure BabelDOC is initialized
try:
    babeldoc_init()
except Exception as e:
    app_logger.warning(f"BabelDOC initialization warning: {e}")


class _CallbackTranslator(BaseTranslator):
    """Routes BabelDOC's per-paragraph translate calls into LinguaHaru.

    Raises NotImplementedError in do_llm_translate() so BabelDOC uses
    translate(), which passes raw paragraph text instead of constructed
    prompts.
    """
    name = "linguaharu"  # Must be <= 20 chars for cache

    def __init__(self, lang_in, lang_out, translate_callback, cache_key_parts):
        # ignore_cache=False: BabelDOC's SQLite cache dedupes repeated
        # paragraphs and makes re-runs of the same document nearly free
        super().__init__(lang_in, lang_out, ignore_cache=False)
        self._translate_callback = translate_callback
        for key, value in cache_key_parts.items():
            self.add_cache_impact_parameters(key, value)

    def do_translate(self, text, rate_limit_params=None):
        return self._translate_callback(text)

    def do_llm_translate(self, text, rate_limit_params=None):
        raise NotImplementedError("Use do_translate instead")


class PdfTranslator(DocumentTranslator):
    """PDF translator: single BabelDOC pass with LinguaHaru's translation backend."""

    def __init__(self, input_file_path, model, use_online, api_key, src_lang, dst_lang,
                 continue_mode, max_token, max_retries, thread_count, glossary_path,
                 temp_dir, result_dir, session_lang="en", log_dir="log",
                 word_bilingual_mode=False):
        super().__init__(
            input_file_path, model, use_online, api_key, src_lang, dst_lang,
            continue_mode, max_token, max_retries, thread_count, glossary_path,
            temp_dir, result_dir, session_lang, log_dir
        )

        self.word_bilingual_mode = word_bilingual_mode
        self.babeldoc_working_dir = os.path.join(self.temp_dir, "babeldoc_working")
        self.doc_layout_model = None

        # Output paths
        self.mono_pdf_path = None
        self.dual_pdf_path = None

        # Cooperative cancellation shared with BabelDOC
        self._cancel_event = threading.Event()

        # Glossary, loaded once and matched per paragraph
        self._glossary_entries = []
        if glossary_path and os.path.exists(glossary_path):
            self._glossary_entries = load_glossary(glossary_path, src_lang, dst_lang)

        # Failed-paragraph tracking (returned as missing_counts)
        self._paragraph_counter = 0
        self._failed_paragraphs = []

    def _ensure_layout_model(self):
        """Lazily load the document layout model."""
        if self.doc_layout_model is None:
            app_logger.info("Loading document layout model...")
            self.doc_layout_model = DocLayoutModel.load_onnx()
        return self.doc_layout_model

    def _translate_paragraph(self, text):
        """Translate one paragraph with LinguaHaru prompts, glossary and retry.

        Runs inside BabelDOC's worker pool. Returns the source text unchanged
        on failure (BabelDOC then keeps the original paragraph)."""
        if not text or not text.strip():
            return text

        # Cooperative stop: convert the stop exception into a cancel signal;
        # the main thread re-raises it after BabelDOC aborts
        try:
            self.check_for_stop()
        except Exception:
            self._cancel_event.set()
            raise

        stripped = text.strip()
        if not should_translate(stripped):
            return text

        with self.lock:
            self._paragraph_counter += 1
            paragraph_index = self._paragraph_counter

        glossary_terms = []
        if self._glossary_entries:
            glossary_terms = find_terms_with_hashtable(stripped, self._glossary_entries)

        segment = {"1": stripped}
        # translate_text retries API failures internally; this loop only
        # retries malformed (unparseable) model output
        for attempt in range(2):
            translated_text, success, token_usage = translate_text(
                segment, self.previous_text_default, self.model, self.use_online,
                self.api_key, self.system_prompt, self.user_prompt,
                self.previous_prompt, self.glossary_prompt, glossary_terms,
                check_stop_callback=self._check_stop_and_signal_cancel
            )
            self._add_token_usage(token_usage)

            if not success or not translated_text:
                break

            result = self._parse_single_translation(translated_text)
            if result:
                return result
            app_logger.warning(
                f"Unparseable translation output for paragraph {paragraph_index} "
                f"(attempt {attempt + 1}): {str(translated_text)[:100]}"
            )

        with self.lock:
            self._failed_paragraphs.append(paragraph_index)
        app_logger.warning(f"Paragraph {paragraph_index} kept untranslated: {stripped[:80]}")
        return text

    def _check_stop_and_signal_cancel(self):
        try:
            self.check_for_stop()
        except Exception:
            self._cancel_event.set()
            raise

    @staticmethod
    def _parse_single_translation(raw_output):
        """Extract the translated value from the model's {"1": "..."} reply."""
        cleaned = clean_json(raw_output)
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(data, dict) and data:
            value = next(iter(data.values()))
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _create_babeldoc_config(self, translator):
        os.makedirs(self.babeldoc_working_dir, exist_ok=True)
        os.makedirs(self.result_dir, exist_ok=True)

        return TranslationConfig(
            input_file=self.input_file_path,
            translator=translator,
            lang_in=self.src_lang,
            lang_out=self.dst_lang,
            output_dir=self.result_dir,
            working_dir=self.babeldoc_working_dir,
            doc_layout_model=self._ensure_layout_model(),
            no_dual=not self.word_bilingual_mode,
            no_mono=False,
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            # Concurrency: BabelDOC defaults to qps=4, which throttles the
            # whole translation stage; use the user-selected thread count
            qps=max(1, self.num_threads),
            pool_max_workers=max(1, self.num_threads),
            # Performance: skip stages we don't use
            skip_scanned_detection=True,
            auto_extract_glossary=False,
            debug=False,
        )

    def _make_progress_callback(self, progress_callback):
        """Adapt BabelDOC ProgressMonitor events to the Gradio callback."""
        def on_progress(**kwargs):
            if progress_callback is None:
                return
            try:
                overall = kwargs.get("overall_progress")
                if overall is None:
                    return
                stage = kwargs.get("stage", "") or ""
                desc = f"{self._get_status_message('Translating')}... {stage}"
                # Map BabelDOC 0-100 into 2%-98% of the UI bar
                progress_callback(0.02 + 0.96 * float(overall) / 100.0, desc=desc)
            except Exception:
                # Never let UI reporting break the translation
                pass
        return on_progress

    def process(self, file_name, file_extension, progress_callback=None):
        """Run the single-pass BabelDOC translation."""
        from datetime import datetime
        self.translation_start_time = datetime.now()

        if progress_callback:
            progress_callback(0.0, desc=f"{self._get_status_message('Extracting PDF content')}...")

        translator = _CallbackTranslator(
            self.src_lang, self.dst_lang, self._translate_paragraph,
            cache_key_parts={
                "model": str(self.model),
                "glossary": os.path.basename(self.glossary_path or ""),
            },
        )
        config = self._create_babeldoc_config(translator)

        app_logger.info(
            f"Translating PDF in a single BabelDOC pass "
            f"(workers={self.num_threads}): {self.input_file_path}"
        )

        try:
            stages = get_translation_stage(config)
            with ProgressMonitor(
                stages,
                progress_change_callback=self._make_progress_callback(progress_callback),
                cancel_event=self._cancel_event,
                # on_finish() always sets cancel_event and then invokes
                # finish_callback; without a callable it crashes at the end
                finish_callback=lambda **kwargs: None,
            ) as pm:
                result = do_translate(pm, config)

            # If the run was aborted by a stop request, surface it now
            self.check_for_stop()

            if result:
                self.mono_pdf_path = result.mono_pdf_path
                self.dual_pdf_path = result.dual_pdf_path

        except Exception as e:
            # A stop request may surface as a BabelDOC cancellation error
            self.check_for_stop()
            app_logger.error(f"PDF translation failed: {e}")
            raise
        finally:
            self.cleanup()

        if progress_callback:
            progress_callback(0.99, desc=f"{self._get_status_message('Generating translated PDF')}...")

        self._finalize_output()

        if self._failed_paragraphs:
            app_logger.warning(
                f"{len(self._failed_paragraphs)} paragraphs kept their source text "
                f"(translation failed): {self._failed_paragraphs[:20]}"
            )

        # Pick the output to return
        output_path = None
        if self.word_bilingual_mode and self.dual_pdf_path and os.path.exists(self.dual_pdf_path):
            output_path = str(self.dual_pdf_path)
        elif self.mono_pdf_path and os.path.exists(self.mono_pdf_path):
            output_path = str(self.mono_pdf_path)
        if not output_path:
            raise RuntimeError("BabelDOC did not produce an output PDF")

        self._save_translation_summary(status="success", output_file_path=output_path)

        if progress_callback:
            progress_callback(1.0, desc=self._get_status_message('Translation completed'))

        return output_path, sorted(self._failed_paragraphs)

    def _finalize_output(self):
        """Rename BabelDOC output to the expected result locations."""
        input_name = os.path.splitext(os.path.basename(self.input_file_path))[0]
        lang_suffix = f"{self.src_lang}2{self.dst_lang}"

        final_mono_path = os.path.join(self.result_dir, f"{input_name}_{lang_suffix}.pdf")
        final_dual_path = os.path.join(self.result_dir, f"{input_name}_{lang_suffix}_bilingual.pdf")

        if self.mono_pdf_path and os.path.exists(self.mono_pdf_path):
            if str(self.mono_pdf_path) != final_mono_path:
                if os.path.exists(final_mono_path):
                    os.remove(final_mono_path)
                shutil.move(str(self.mono_pdf_path), final_mono_path)
            self.mono_pdf_path = Path(final_mono_path)

        if self.word_bilingual_mode and self.dual_pdf_path and os.path.exists(self.dual_pdf_path):
            if str(self.dual_pdf_path) != final_dual_path:
                if os.path.exists(final_dual_path):
                    os.remove(final_dual_path)
                shutil.move(str(self.dual_pdf_path), final_dual_path)
            self.dual_pdf_path = Path(final_dual_path)

    def cleanup(self):
        """Clean up temporary BabelDOC working directory."""
        try:
            if os.path.exists(self.babeldoc_working_dir):
                shutil.rmtree(self.babeldoc_working_dir, ignore_errors=True)
                app_logger.info("Cleaned up BabelDOC working directory")
        except Exception as e:
            app_logger.warning(f"Failed to clean up BabelDOC working directory: {e}")
