"""
PDF Translator Module - BabelDOC Two-Pass Implementation

This module implements PDF translation using BabelDOC library with LinguaHaru's
full translation pipeline. It uses a two-pass approach:

Pass 1 (Extraction): BabelDOC parses PDF and extracts source text
    -> Save to src.json
    -> LinguaHaru pipeline: dedupe -> split -> translate -> retry -> restore
    -> dst_translated.json

Pass 2 (Writing): BabelDOC generates PDF with pre-translated text
    -> mono PDF (translated only)
    -> dual PDF (bilingual, if enabled)
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
from textProcessing.base_translator import DocumentTranslator
from config.log_config import app_logger

# Ensure BabelDOC is initialized
try:
    babeldoc_init()
except Exception as e:
    app_logger.warning(f"BabelDOC initialization warning: {e}")


class SourceExtractorTranslator(BaseTranslator):
    """
    A translator that collects source text during BabelDOC extraction.
    Returns text unchanged - just stores it for later use.

    Raises NotImplementedError in do_llm_translate() so BabelDOC uses
    translate() which passes raw text instead of constructed prompts.
    """
    name = "extractor"  # Must be <= 20 chars for cache

    def __init__(self, lang_in, lang_out):
        super().__init__(lang_in, lang_out, ignore_cache=True)
        self.source_texts = []
        self.lock = threading.Lock()

    def do_translate(self, text, rate_limit_params=None):
        """Collect source text and return unchanged."""
        if text and text.strip():
            with self.lock:
                self.source_texts.append(text)
        return text  # Return unchanged for extraction pass

    def do_llm_translate(self, text, rate_limit_params=None):
        """Raise NotImplementedError to force BabelDOC to use do_translate with raw text."""
        raise NotImplementedError("Use do_translate instead")

    def get_collected_texts(self):
        """Return all collected source texts."""
        with self.lock:
            return list(self.source_texts)


class TranslatedLookupTranslator(BaseTranslator):
    """
    A translator that returns pre-translated text from a lookup map.
    Used in Pass 2 to generate the final PDF with translations.

    Raises NotImplementedError in do_llm_translate() so BabelDOC uses
    translate() which passes raw text.
    """
    name = "lookup"  # Must be <= 20 chars for cache

    def __init__(self, lang_in, lang_out, translation_map):
        super().__init__(lang_in, lang_out, ignore_cache=True)
        self.translation_map = translation_map  # {source_text: translated_text}

    def do_translate(self, text, rate_limit_params=None):
        """Look up and return translated text."""
        if text in self.translation_map:
            return self.translation_map[text]
        # If not found in map, return original text
        app_logger.warning(f"Translation not found for text: {text[:50]}...")
        return text

    def do_llm_translate(self, text, rate_limit_params=None):
        """Raise NotImplementedError to force BabelDOC to use do_translate with raw text."""
        raise NotImplementedError("Use do_translate instead")


class PdfTranslator(DocumentTranslator):
    """
    PDF translator using BabelDOC library with LinguaHaru's translation backend.

    This translator uses a two-pass approach:
    1. Pass 1: BabelDOC extracts source text -> src.json
    2. LinguaHaru pipeline translates the text
    3. Pass 2: BabelDOC generates PDF with translated text
    """

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
        self.extractor = None

        # Output paths
        self.mono_pdf_path = None
        self.dual_pdf_path = None

        # Progress phase tracking for scaling
        self._current_phase = "extraction"

    def _ensure_layout_model(self):
        """Lazily load the document layout model."""
        if self.doc_layout_model is None:
            app_logger.info("Loading document layout model...")
            self.doc_layout_model = DocLayoutModel.load_onnx()
        return self.doc_layout_model

    def _create_babeldoc_config(self, translator, output_dir=None, extraction_only=False):
        """Create BabelDOC TranslationConfig with the given translator.

        Args:
            translator: The translator instance to use
            output_dir: Output directory for generated files
            extraction_only: If True, skip PDF generation (for Pass 1)
        """
        if output_dir is None:
            output_dir = self.result_dir

        os.makedirs(self.babeldoc_working_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # For extraction pass, skip all PDF output
        if extraction_only:
            no_mono = True
            no_dual = True
        else:
            no_mono = False
            no_dual = not self.word_bilingual_mode

        config = TranslationConfig(
            input_file=self.input_file_path,
            translator=translator,
            lang_in=self.src_lang,
            lang_out=self.dst_lang,
            output_dir=output_dir,
            working_dir=self.babeldoc_working_dir,
            doc_layout_model=self._ensure_layout_model(),
            no_dual=no_dual,
            no_mono=no_mono,
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            # Performance optimizations (keep PDF cleaning for smaller file size):
            skip_scanned_detection=True,  # Skip scanned document detection
            auto_extract_glossary=False,  # Skip automatic term extraction
            debug=False,  # Disable debug mode for faster processing
        )
        return config

    def _extract_from_tracking_file(self):
        """
        Fallback: Extract source texts from BabelDOC's translate_tracking.json file.
        The file structure is:
        {
            "page": [{"paragraph": [{"input": "text", ...}, ...]}, ...],
            "cross_page": [...],
            "cross_column": [...]
        }
        """
        source_texts = []
        tracking_file = os.path.join(self.babeldoc_working_dir, "translate_tracking.json")

        if not os.path.exists(tracking_file):
            # Search in subdirectories
            for root, dirs, files in os.walk(self.babeldoc_working_dir):
                if "translate_tracking.json" in files:
                    tracking_file = os.path.join(root, "translate_tracking.json")
                    break

        if os.path.exists(tracking_file):
            try:
                with open(tracking_file, 'r', encoding='utf-8') as f:
                    tracking_data = json.load(f)

                # Extract from page -> paragraph -> input
                def extract_paragraphs(page_list):
                    texts = []
                    for page in page_list:
                        if isinstance(page, dict) and 'paragraph' in page:
                            for para in page['paragraph']:
                                if isinstance(para, dict) and 'input' in para:
                                    input_text = para['input']
                                    if isinstance(input_text, str) and input_text.strip():
                                        texts.append(input_text.strip())
                    return texts

                # Extract from all sections
                if 'page' in tracking_data:
                    source_texts.extend(extract_paragraphs(tracking_data['page']))
                if 'cross_page' in tracking_data:
                    source_texts.extend(extract_paragraphs(tracking_data['cross_page']))
                if 'cross_column' in tracking_data:
                    source_texts.extend(extract_paragraphs(tracking_data['cross_column']))

                app_logger.info(f"Extracted {len(source_texts)} texts from tracking file: {tracking_file}")

            except Exception as e:
                app_logger.warning(f"Failed to read tracking file: {e}")

        return source_texts

    def extract_content_to_json(self, progress_callback=None):
        """
        Pass 1: Use BabelDOC to extract source text from PDF.
        Save extracted text to src.json in LinguaHaru format.
        Progress: 0.0-0.1 (0-10%)
        """
        if progress_callback:
            progress_callback(0.0, desc=f"{self._get_status_message('Extracting PDF content')}...")

        # Create source extractor translator
        self.extractor = SourceExtractorTranslator(self.src_lang, self.dst_lang)

        # Create BabelDOC config for extraction only (no PDF output)
        extraction_output_dir = os.path.join(self.babeldoc_working_dir, "extraction_pass")
        config = self._create_babeldoc_config(self.extractor, output_dir=extraction_output_dir, extraction_only=True)

        app_logger.info(f"Pass 1: Extracting source text from {self.input_file_path}")

        if progress_callback:
            progress_callback(0.05, desc=f"{self._get_status_message('Extracting PDF content')}...")

        source_texts = []
        babeldoc_error = None

        try:
            # Run BabelDOC extraction pass
            stages = get_translation_stage(config)
            with ProgressMonitor(stages) as pm:
                do_translate(pm, config)

            # Get collected source texts from translator
            source_texts = self.extractor.get_collected_texts()
            app_logger.info(f"Collected {len(source_texts)} text segments from translator")

        except Exception as e:
            babeldoc_error = e
            app_logger.warning(f"BabelDOC extraction encountered error: {e}")
            # Don't raise yet - try to extract from tracking file first

        # Always try to extract from tracking file (it's created before font subsetting)
        # This serves as both a fallback and a verification
        tracking_texts = self._extract_from_tracking_file()

        # Use tracking file texts if translator didn't collect or collected fewer
        if tracking_texts and len(tracking_texts) > len(source_texts):
            app_logger.info(f"Using tracking file extraction ({len(tracking_texts)} texts) instead of translator ({len(source_texts)} texts)")
            source_texts = tracking_texts
        elif not source_texts and tracking_texts:
            source_texts = tracking_texts

        if not source_texts:
            raise RuntimeError("Failed to extract any text from PDF")

        app_logger.info(f"Total extracted: {len(source_texts)} text segments")

        if progress_callback:
            progress_callback(0.08, desc=f"{self._get_status_message('Extracting PDF content')}...")

        # Save to src.json in LinguaHaru format
        os.makedirs(self.file_dir, exist_ok=True)
        src_data = []
        for i, text in enumerate(source_texts, start=1):
            src_data.append({
                "count_src": i,
                "value": text,
                "type": "text"
            })

        with open(self.src_json_path, 'w', encoding='utf-8') as f:
            json.dump(src_data, f, ensure_ascii=False, indent=4)

        app_logger.info(f"Saved extracted text to {self.src_json_path}")

        if progress_callback:
            progress_callback(0.1, desc=f"{self._get_status_message('Extracting PDF content')}...")

        # Switch to translation phase for progress scaling
        self._current_phase = "translation"

        return self.src_json_path

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        """
        Pass 2: Use BabelDOC to generate PDF with translated text.
        Loads translations from dst_translated.json and uses lookup translator.
        Progress: 90-100%
        """
        # Switch to PDF generation phase (progress values pass through unchanged)
        self._current_phase = "pdf_generation"

        if progress_callback:
            progress_callback(0.9, desc=f"{self._get_status_message('Generating translated PDF')}...")

        # Load translations from dst_translated.json
        translation_map = {}
        try:
            with open(translated_json_path, 'r', encoding='utf-8') as f:
                translated_data = json.load(f)

            for item in translated_data:
                original = item.get('original', '')
                translated = item.get('translated', '')
                if original and translated:
                    translation_map[original] = translated

            app_logger.info(f"Loaded {len(translation_map)} translations")

        except Exception as e:
            app_logger.error(f"Failed to load translations: {e}")
            raise

        if progress_callback:
            progress_callback(0.92, desc=f"{self._get_status_message('Generating translated PDF')}...")

        # Create lookup translator with pre-translated text
        lookup_translator = TranslatedLookupTranslator(
            self.src_lang, self.dst_lang, translation_map
        )

        # Create BabelDOC config for PDF generation
        config = self._create_babeldoc_config(lookup_translator, output_dir=self.result_dir)

        app_logger.info(f"Pass 2: Generating translated PDF for {self.input_file_path}")

        try:
            # Run BabelDOC write pass
            stages = get_translation_stage(config)
            with ProgressMonitor(stages) as pm:
                result = do_translate(pm, config)

            # Get output paths from result
            if result:
                self.mono_pdf_path = result.mono_pdf_path
                self.dual_pdf_path = result.dual_pdf_path

                app_logger.info(f"BabelDOC PDF generation completed")
                if self.mono_pdf_path:
                    app_logger.info(f"Mono PDF: {self.mono_pdf_path}")
                if self.dual_pdf_path:
                    app_logger.info(f"Dual PDF: {self.dual_pdf_path}")

        except Exception as e:
            app_logger.error(f"BabelDOC PDF generation failed: {e}")
            # Try to find any generated PDF files in output directory
            self._try_recover_output_pdfs()
            if not self.mono_pdf_path:
                raise

        if progress_callback:
            progress_callback(0.98, desc=f"{self._get_status_message('Generating translated PDF')}...")

        # Rename output files to expected locations
        self._finalize_output()

        if progress_callback:
            progress_callback(1.0, desc=f"{self._get_status_message('Generating translated PDF')}...")

    def _try_recover_output_pdfs(self):
        """Try to find any PDF files that may have been generated before failure."""
        try:
            input_name = os.path.splitext(os.path.basename(self.input_file_path))[0]

            # Search for any PDF files in result directory
            for f in os.listdir(self.result_dir):
                if f.endswith('.pdf') and input_name in f:
                    pdf_path = os.path.join(self.result_dir, f)
                    if 'dual' in f.lower() or 'bilingual' in f.lower():
                        self.dual_pdf_path = Path(pdf_path)
                        app_logger.info(f"Recovered dual PDF: {pdf_path}")
                    else:
                        self.mono_pdf_path = Path(pdf_path)
                        app_logger.info(f"Recovered mono PDF: {pdf_path}")

            # Also search in babeldoc working directory
            for root, dirs, files in os.walk(self.babeldoc_working_dir):
                for f in files:
                    if f.endswith('.pdf') and input_name in f:
                        pdf_path = os.path.join(root, f)
                        if 'dual' in f.lower():
                            if not self.dual_pdf_path:
                                self.dual_pdf_path = Path(pdf_path)
                                app_logger.info(f"Recovered dual PDF from working dir: {pdf_path}")
                        elif not self.mono_pdf_path:
                            self.mono_pdf_path = Path(pdf_path)
                            app_logger.info(f"Recovered mono PDF from working dir: {pdf_path}")

        except Exception as e:
            app_logger.warning(f"Failed to recover output PDFs: {e}")

    def _finalize_output(self):
        """Rename BabelDOC output to the expected result locations."""
        # Determine output filename
        input_name = os.path.splitext(os.path.basename(self.input_file_path))[0]
        lang_suffix = f"{self.src_lang}2{self.dst_lang}"

        # Expected output path
        final_mono_path = os.path.join(self.result_dir, f"{input_name}_{lang_suffix}.pdf")
        final_dual_path = os.path.join(self.result_dir, f"{input_name}_{lang_suffix}_bilingual.pdf")

        # Rename mono PDF if exists (use rename instead of copy for efficiency)
        if self.mono_pdf_path and os.path.exists(self.mono_pdf_path):
            if str(self.mono_pdf_path) != final_mono_path:
                # Remove existing file if exists
                if os.path.exists(final_mono_path):
                    os.remove(final_mono_path)
                shutil.move(str(self.mono_pdf_path), final_mono_path)
                app_logger.info(f"Renamed mono PDF to {final_mono_path}")
            self.mono_pdf_path = Path(final_mono_path)

        # Rename dual PDF if exists and bilingual mode is enabled
        if self.word_bilingual_mode and self.dual_pdf_path and os.path.exists(self.dual_pdf_path):
            if str(self.dual_pdf_path) != final_dual_path:
                # Remove existing file if exists
                if os.path.exists(final_dual_path):
                    os.remove(final_dual_path)
                shutil.move(str(self.dual_pdf_path), final_dual_path)
                app_logger.info(f"Renamed dual PDF to {final_dual_path}")
            self.dual_pdf_path = Path(final_dual_path)

    def cleanup(self):
        """Clean up temporary BabelDOC working directory."""
        try:
            if os.path.exists(self.babeldoc_working_dir):
                shutil.rmtree(self.babeldoc_working_dir, ignore_errors=True)
                app_logger.info(f"Cleaned up BabelDOC working directory")
        except Exception as e:
            app_logger.warning(f"Failed to clean up BabelDOC working directory: {e}")

    def process(self, file_name, file_extension, progress_callback=None):
        """
        Process PDF translation using the two-pass approach.

        Overrides the base class process to:
        1. Pass 1: extract_content_to_json() - BabelDOC extracts text (0-10%)
        2. Base class handles: dedupe -> split -> translate -> retry -> restore (10-90%)
        3. Pass 2: write_translated_json_to_file() - BabelDOC generates PDF (90-100%)
        """
        # Create a progress wrapper that scales translation progress (0.0-1.0) to (0.1-0.9)
        # but passes through other phases unchanged (extraction: 0.0-0.1, PDF gen: 0.9-1.0)
        self._current_phase = "extraction"  # Track which phase we're in

        def scaled_progress_callback(progress, desc=""):
            if progress_callback is None:
                return

            # During translation phase, scale 0.0-1.0 to 0.1-0.9
            if self._current_phase == "translation":
                # Scale: 0.0 -> 0.1, 1.0 -> 0.9
                scaled_progress = 0.1 + (progress * 0.8)
                progress_callback(scaled_progress, desc=desc)
            else:
                # Extraction (0.0-0.1) and PDF generation (0.9-1.0) pass through unchanged
                progress_callback(progress, desc=desc)

        try:
            # Call base class process which will:
            # - Call our overridden extract_content_to_json (Pass 1)
            # - Run translation pipeline (dedupe, split, translate, retry, restore)
            # - Call our overridden write_translated_json_to_file (Pass 2)
            base_output_path, missing_counts = super().process(file_name, file_extension, scaled_progress_callback)

            # Return the actual PDF output path (set by write_translated_json_to_file)
            # If bilingual mode is enabled and dual PDF exists, return that
            if self.word_bilingual_mode and self.dual_pdf_path and os.path.exists(self.dual_pdf_path):
                return str(self.dual_pdf_path), missing_counts
            # Otherwise return mono PDF
            if self.mono_pdf_path and os.path.exists(self.mono_pdf_path):
                return str(self.mono_pdf_path), missing_counts

            return base_output_path, missing_counts

        except Exception as e:
            app_logger.error(f"PDF translation failed: {e}")
            raise
        finally:
            # Cleanup BabelDOC temp files
            self.cleanup()
