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
from core.engine.translation_checker import clean_json, is_translation_valid
from core.log_config import app_logger
from core import model_store

# Only ONE PDF at a time may claim the per-project log "fallback" (BabelDOC's
# internal threads carry no task context). A concurrent PDF skips it (its
# babeldoc warnings still reach system.log) rather than corrupting the first
# PDF's log — and without blocking, so the 2nd PDF isn't stalled.
_BABELDOC_FALLBACK_LOCK = threading.Lock()

# Redirect BabelDOC's hardcoded ~/.cache/babeldoc into the unified models dir
# BEFORE init / first download, so the PDF layout model + fonts live alongside
# the other engines' models (data/models, or the user-chosen location).
model_store.redirect_babeldoc_cache()

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
                 word_bilingual_mode=False, history_dir=None,
                 batch_id=None, batch_size=None):
        super().__init__(
            input_file_path, model, use_online, api_key, src_lang, dst_lang,
            continue_mode, max_token, max_retries, thread_count, glossary_path,
            temp_dir, result_dir, session_lang, log_dir, history_dir=history_dir,
            batch_id=batch_id, batch_size=batch_size
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
        # (original, translated) pairs for the post-run QA report
        self._qa_pairs = []

    def _preflight_check(self):
        """Catch the two silent-failure PDFs before the expensive BabelDOC run.

        - Encrypted/password-protected: BabelDOC would fail with an opaque
          error deep inside parsing.
        - Scanned / image-only (no text layer): with skip_scanned_detection
          BabelDOC produces an output with nothing translated. Surface a clear,
          actionable error instead of a silently-empty result.
        """
        import pymupdf

        try:
            doc = pymupdf.open(self.input_file_path)
        except Exception as e:
            raise RuntimeError(f"Could not open the PDF (it may be corrupted): {e}")

        try:
            if getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False):
                # An empty password unlocks many "encrypted" PDFs (owner-only
                # restrictions); only fail when that does not work.
                if not doc.authenticate(""):
                    raise RuntimeError(
                        "This PDF is password-protected. Remove the password and try again."
                    )

            # Sample the first pages for an extractable text layer. Scanned
            # PDFs render as images and yield no text — UNLESS OCR is enabled
            # (pdf_ocr_scanned), in which case BabelDOC's OCR workaround handles
            # the image-only pages, so we must NOT reject them here.
            ocr_enabled = False
            try:
                from core.paths import SYSTEM_CONFIG
                with open(SYSTEM_CONFIG, encoding="utf-8") as _f:
                    ocr_enabled = bool(json.load(_f).get("pdf_ocr_scanned", False))
            except Exception:  # noqa: BLE001
                pass
            sample_pages = min(len(doc), 10)
            has_text = any(doc[i].get_text().strip() for i in range(sample_pages))
            if not has_text and not ocr_enabled:
                raise RuntimeError(
                    "This PDF has no extractable text (it looks scanned or image-only). "
                    "Enable 'OCR scanned PDF' in PDF options, or use the image translation "
                    "feature for scanned documents."
                )
        finally:
            doc.close()

    def _ensure_layout_model(self):
        """Lazily load the document layout model."""
        if self.doc_layout_model is None:
            app_logger.info("Loading document layout model...")
            self.doc_layout_model = DocLayoutModel.load_onnx()
        return self.doc_layout_model

    def _maybe_extract_glossary(self, progress_callback=None):
        """If enabled, pull the PDF's text layer (pymupdf) BEFORE the BabelDOC run
        and AI-extract terms, merging them into the glossary used per paragraph —
        so PDF gets the same auto-glossary as documents."""
        try:
            from core.paths import SYSTEM_CONFIG
            with open(SYSTEM_CONFIG, encoding="utf-8") as f:
                if not json.load(f).get("auto_extract_glossary", False):
                    return
        except Exception:  # noqa: BLE001
            return
        self.check_for_stop()
        try:
            import pymupdf
            from core.engine.glossary_extractor import extract_glossary_terms, write_merged_glossary
            from core.engine.text_separator import load_glossary
            if progress_callback:
                progress_callback(0.0, desc=f"{self._get_status_message('Extracting glossary')}...")
            doc = pymupdf.open(self.input_file_path)
            try:
                values = [doc[i].get_text().strip() for i in range(len(doc))]
            finally:
                doc.close()
            values = [v for v in values if v]
            terms = extract_glossary_terms(
                values, self.model, self.use_online, self.api_key,
                self.src_lang, self.dst_lang, check_stop=self.check_for_stop,
                mode_params=self.topts.get("params"))
            if not terms:
                return
            user_entries = list(self._glossary_entries)
            merged_path = os.path.join(self.file_dir, "auto_glossary.csv")
            write_merged_glossary(terms, user_entries, merged_path, self.src_lang, self.dst_lang)
            self.glossary_path = merged_path
            self._glossary_entries = load_glossary(merged_path, self.src_lang, self.dst_lang)
            review = os.path.splitext(os.path.basename(self.input_file_path))[0] + "_glossary.csv"
            try:
                shutil.copyfile(merged_path, os.path.join(self.result_dir, review))
            except OSError:
                pass
            app_logger.info(f"PDF: using merged glossary with {len(terms)} AI-extracted terms")
        except Exception as e:  # noqa: BLE001 — glossary is best-effort
            app_logger.warning(f"PDF glossary extraction skipped: {e}")

    def _write_qa_report(self):
        """Run the active mode's QA checks over this PDF's (original, translated)
        pairs and write qa.json. Best-effort, never raises."""
        try:
            qa_list = self.topts.get("params", {}).get("qa", [])
            if not qa_list or not self._qa_pairs:
                return
            from core.engine import translation_qa
            dst_items = [{"count_src": i, "original": src, "translated": dst}
                         for i, (src, dst) in enumerate(self._qa_pairs)]
            warns = translation_qa.run(qa_list, dst_items, self._glossary_entries)
            if warns:
                app_logger.info("QA warnings (" + ", ".join(
                    f"{k}: {len(v)}" for k, v in warns.items()) + ")")
            os.makedirs(self.result_dir, exist_ok=True)
            out_path = os.path.join(self.result_dir, "qa.json")
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(warns, f, ensure_ascii=False, indent=2)
            os.replace(tmp, out_path)
        except Exception as e:  # noqa: BLE001 — QA is advisory
            app_logger.warning(f"PDF QA report skipped: {e}")

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
                check_stop_callback=self._check_stop_and_signal_cancel,
                options=self.topts,   # apply the run's mode sampling/tone/length too
            )
            self._add_token_usage(token_usage)

            if not success or not translated_text:
                break

            result = self._parse_single_translation(translated_text)
            # Apply the SAME validation as the document path: drop placeholders/
            # formula markers, copied source, or wrong-language output -> retry,
            # then keep the source paragraph rather than shipping a broken one.
            if result and not is_translation_valid(stripped, result, self.src_lang, self.dst_lang):
                app_logger.warning(
                    f"Paragraph {paragraph_index} failed validation (attempt {attempt + 1})")
                result = None
            if result:
                # Optional polish second pass (same gating as the document path).
                if self.topts.get("params", {}).get("second_pass"):
                    try:
                        from core.llm.llm_wrapper import polish_translation
                        polished, pol_usage = polish_translation(
                            json.dumps({"1": result}, ensure_ascii=False),
                            self.dst_lang, self.model, self.use_online, self.api_key,
                            check_stop=self._check_stop_and_signal_cancel, options=self.topts)
                        self._add_token_usage(pol_usage)
                        pv = self._parse_single_translation(polished)
                        # Re-validate the polish: a second pass can drop
                        # placeholders, copy the source, or emit the wrong language.
                        # Only accept it if it passes the SAME check as the first
                        # pass; otherwise keep the already-validated result.
                        if pv and is_translation_valid(stripped, pv, self.src_lang, self.dst_lang):
                            result = pv
                        elif pv:
                            app_logger.warning(
                                f"Paragraph {paragraph_index} polish failed validation; keeping first pass")
                    except Exception as e:  # noqa: BLE001 — never break on polish
                        app_logger.warning(f"PDF polish skipped: {e}")
                with self.lock:
                    self._qa_pairs.append((stripped, result))
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
        """Extract the translated value from the model's {"1": "..."} reply. Require
        the exact key "1" (the segment key) — accepting any dict's first value let a
        stray/renamed key through as if it were the translation."""
        cleaned = clean_json(raw_output)
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(data, dict):
            value = data.get("1")
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _pdf_options():
        """User-set PDF/BabelDOC options from system_config (all default off)."""
        try:
            from core.paths import SYSTEM_CONFIG
            with open(SYSTEM_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:  # noqa: BLE001
            cfg = {}
        return {
            "table": bool(cfg.get("pdf_translate_table", False)),
            "ocr": bool(cfg.get("pdf_ocr_scanned", False)),
            "alternating": bool(cfg.get("pdf_dual_alternating", False)),
            "pages": (cfg.get("pdf_pages") or "").strip(),
            "only_translated": bool(cfg.get("pdf_only_translated_pages", False)),
        }

    def _create_babeldoc_config(self, translator):
        os.makedirs(self.babeldoc_working_dir, exist_ok=True)
        os.makedirs(self.result_dir, exist_ok=True)

        opt = self._pdf_options()
        bilingual = bool(self.word_bilingual_mode)

        kwargs = dict(
            input_file=self.input_file_path,
            translator=translator,
            lang_in=self.src_lang,
            lang_out=self.dst_lang,
            output_dir=self.result_dir,
            working_dir=self.babeldoc_working_dir,
            doc_layout_model=self._ensure_layout_model(),
            no_dual=not bilingual,
            no_mono=False,
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            # Concurrency: BabelDOC defaults to qps=4, which throttles the
            # whole translation stage; use the user-selected thread count
            qps=max(1, self.num_threads),
            pool_max_workers=max(1, self.num_threads),
            auto_extract_glossary=False,
            debug=False,
        )

        # Scanned/image PDF: enable OCR (else we skip scan detection for speed).
        if opt["ocr"]:
            kwargs["auto_enable_ocr_workaround"] = True
            kwargs["skip_scanned_detection"] = False
        else:
            kwargs["skip_scanned_detection"] = True

        # Table text translation (experimental, slower) — RapidOCR table model.
        if opt["table"]:
            try:
                from babeldoc.docvision.table_detection.rapidocr import RapidOCRModel
                kwargs["table_model"] = RapidOCRModel()
            except Exception as e:  # noqa: BLE001
                app_logger.warning(f"Table model unavailable, skipping table OCR: {e}")

        # Bilingual layout: alternating original/translated pages (vs side-by-side).
        if bilingual and opt["alternating"]:
            kwargs["use_alternating_pages_dual"] = True

        # Page range (e.g. "1-3,5") + optionally output only the translated pages.
        if opt["pages"]:
            kwargs["pages"] = opt["pages"]
            if opt["only_translated"]:
                kwargs["only_include_translated_page"] = True

        return TranslationConfig(**kwargs)

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

    def _process_impl(self, file_name, file_extension, progress_callback=None):
        """Run the single-pass BabelDOC translation."""
        from datetime import datetime
        self.translation_start_time = datetime.now()

        # Fail fast on encrypted / scanned PDFs instead of silently producing
        # an empty or untranslated output.
        self._preflight_check()

        # Optional: AI-extract a glossary from the PDF's text layer first, so the
        # per-paragraph translation below uses it (same as the document path).
        self._maybe_extract_glossary(progress_callback)

        if progress_callback:
            progress_callback(0.0, desc=f"{self._get_status_message('Extracting PDF content')}...")

        # Cache key must cover EVERYTHING that changes a paragraph's translation —
        # otherwise BabelDOC's SQLite cache reuses a stale result after the user
        # edits the (same-named) glossary, switches translation mode/sampling, or
        # changes languages. Include a glossary CONTENT hash + mode/params + langs.
        import hashlib as _hl
        _gloss_hash = ""
        try:
            if self.glossary_path and os.path.exists(self.glossary_path):
                with open(self.glossary_path, "rb") as _gf:
                    _gloss_hash = _hl.sha1(_gf.read()).hexdigest()[:12]
        except Exception:  # noqa: BLE001
            _gloss_hash = ""
        _params = (self.topts or {}).get("params", {}) if hasattr(self, "topts") else {}
        translator = _CallbackTranslator(
            self.src_lang, self.dst_lang, self._translate_paragraph,
            cache_key_parts={
                "model": str(self.model),
                "glossary": _gloss_hash,
                "langs": f"{self.src_lang}>{self.dst_lang}",
                "mode": str((self.topts or {}).get("mode", "")) if hasattr(self, "topts") else "",
                "temp": str(_params.get("temperature")),
                "top_p": str(_params.get("top_p")),
            },
        )
        config = self._create_babeldoc_config(translator)

        app_logger.info(
            f"Translating PDF in a single BabelDOC pass "
            f"(workers={self.num_threads}): {self.input_file_path}"
        )

        # Capture BabelDOC's own logs: always route its logger into the system
        # log (WARNING+) so failures are monitorable. To also land its internal
        # worker threads' logs in THIS project's log, claim the fallback — but
        # only if no other PDF holds it (non-blocking), so concurrent PDFs never
        # corrupt each other's log.
        from core import log_config
        log_config.file_logger.attach_to_logger("babeldoc")
        got_fallback = _BABELDOC_FALLBACK_LOCK.acquire(blocking=False)
        if got_fallback:
            log_config.file_logger.set_fallback_task(self.translation_id)

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
            if got_fallback:
                log_config.file_logger.clear_fallback_task()
                _BABELDOC_FALLBACK_LOCK.release()
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
        self._write_qa_report()   # mode-aware QA over the translated paragraphs

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
