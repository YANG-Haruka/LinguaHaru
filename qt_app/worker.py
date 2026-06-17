"""QThread worker that runs one file's translation off the UI thread.

Signals:
    progress(float, str)   -- 0..1 fraction and a status description
    finished(str, list)    -- output path, list of missing-segment keys
    failed(str)            -- friendly error message

Stop is honored by wiring `translator.check_stop_requested` to a flag the UI
sets via request_stop(); the translator raises when it sees the flag, which we
turn into a clean "stopped" failure. HardApiError is mapped to a friendly,
actionable message (mirrors app.translate_files).
"""

import os
import json
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal

from core.llm.online_translation import HardApiError, classify_fatal_error
from core import backend


class InstallWorker(QThread):
    """Runs ``pip install -r requirements-*.txt`` for an optional module off the
    UI thread, streaming output lines and reporting success/failure.

    Signals:
        line(str)        -- a line of pip output
        finished_ok(bool, str) -- (success, final message)
    """

    line = Signal(str)
    finished_ok = Signal(bool, str)

    def __init__(self, module_name, action="install", parent=None):
        super().__init__(parent)
        self.module_name = module_name
        self.action = action
        self.freed_bytes = 0   # disk space freed by model cleanup (uninstall)

    def run(self):
        import sys
        from core.module_manager import MODULE_SPECS
        spec = MODULE_SPECS.get(self.module_name)
        if not spec:
            self.finished_ok.emit(False, f"Unknown module: {self.module_name}")
            return
        reqfile, _packages = spec
        if self.action == "uninstall":
            # Only remove deps NOT shared with another plugin (keep the shared STT
            # stack while a sibling still needs it).
            from core.module_manager import packages_to_uninstall
            packages = packages_to_uninstall(self.module_name)
            if not packages:
                self.line.emit("All dependencies are shared with another plugin; kept.")
            else:
                cmd = [sys.executable, "-m", "pip", "uninstall", "-y", *packages]
                if not self._stream(cmd):
                    return
            # Delete this plugin's NON-shared models (OCR/PDF); shared STT kept.
            try:
                from core.optional_modules import cleanup_plugin_models
                removed, freed = cleanup_plugin_models(self.module_name)
                self.freed_bytes = freed
                if removed:
                    self.line.emit("Removed models: " + ", ".join(removed))
            except Exception as e:  # noqa: BLE001
                self.line.emit(f"Model cleanup skipped: {e}")
            self.finished_ok.emit(True, "Uninstall finished")
            return
        elif self.action == "upgrade":
            cmd = [sys.executable, "-m", "pip", "install", "-U", "-r", reqfile]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-r", reqfile]
        if self._stream(cmd):
            self.finished_ok.emit(True, "Installation finished")

    def _stream(self, cmd):
        """Run a pip command, streaming output lines. Returns True on success;
        emits finished_ok(False, ...) and returns False on failure."""
        self.line.emit("$ " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, cwd=backend.REPO_ROOT, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            for raw in proc.stdout:
                self.line.emit(raw.rstrip())
            proc.wait()
        except Exception as e:  # noqa: BLE001 - surface any launch failure
            self.finished_ok.emit(False, f"Error: {e}")
            return False
        if proc.returncode != 0:
            self.finished_ok.emit(False, f"pip exited with code {proc.returncode}")
            return False
        return True


class ModelDownloadWorker(QThread):
    """Downloads (and warms) a plugin's model off the UI thread, by calling
    ``optional_modules.download_plugin_model(name, model_id)``. Heavy + blocking.

    If ``model_id`` is given it is persisted first (inside the backend call);
    pass None to download the plugin's currently-selected/default model (used
    right after a fresh install).

    Signal:
        finished_ok(bool) -- True if the model is ready
    """

    finished_ok = Signal(bool)

    def __init__(self, module_name, model_id=None, parent=None):
        super().__init__(parent)
        self.module_name = module_name
        self.model_id = model_id

    def run(self):
        from core.optional_modules import download_plugin_model
        try:
            ok = bool(download_plugin_model(self.module_name, self.model_id))
        except Exception:  # noqa: BLE001 - a failed download just reports not-ready
            ok = False
        self.finished_ok.emit(ok)


class ModelDeleteWorker(QThread):
    """Deletes a specific model's files off the UI thread via
    ``optional_modules.delete_plugin_model(name, model_id)``."""

    finished_ok = Signal(bool)

    def __init__(self, module_name, model_id, parent=None):
        super().__init__(parent)
        self.module_name = module_name
        self.model_id = model_id

    def run(self):
        from core.optional_modules import delete_plugin_model
        try:
            ok = bool(delete_plugin_model(self.module_name, self.model_id))
        except Exception:  # noqa: BLE001
            ok = False
        self.finished_ok.emit(ok)


class PluginSpaceWorker(QThread):
    """Computes a plugin's library (pip deps) + model disk volumes off the UI
    thread — the pip-deps stat-walk is slow (~seconds) the first time.

    Signal:
        result(str, dict) -- (plugin name, plugin_space() dict; {} on failure)
    """

    result = Signal(str, dict)

    def __init__(self, module_name, parent=None):
        super().__init__(parent)
        self.module_name = module_name

    def run(self):
        try:
            from core.optional_modules import plugin_space
            self.result.emit(self.module_name, plugin_space(self.module_name))
        except Exception:  # noqa: BLE001
            self.result.emit(self.module_name, {})


class ModuleUpdateCheckWorker(QThread):
    """Checks PyPI for a newer version of an installed module's package, off the
    UI thread (the network call can block for seconds).

    Signal:
        result(str, dict) -- (module name, check_module_update() dict; {} if none)
    """

    result = Signal(str, dict)

    def __init__(self, module_name, parent=None):
        super().__init__(parent)
        self.module_name = module_name

    def run(self):
        from core.module_manager import check_module_update
        try:
            info = check_module_update(self.module_name) or {}
        except Exception:  # noqa: BLE001 - a failed check just shows nothing
            info = {}
        self.result.emit(self.module_name, info)


class QuickTranslateWorker(QThread):
    """Translate one short text off the UI thread via core.quick_translate.

    Signals:
        done(str, bool) -- (translated, ok)
    """
    done = Signal(str, bool)

    def __init__(self, text, src_lang, dst_lang, parent=None, context=""):
        super().__init__(parent)
        self.text = text
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.context = context

    def run(self):
        from core import quick_translate
        try:
            translated, ok = quick_translate.translate(
                self.text, self.src_lang, self.dst_lang, context=self.context)
        except Exception as e:  # noqa: BLE001 - surface as a failed translation
            self.done.emit(f"Error: {e}", False)
            return
        self.done.emit(translated, ok)


class TtsWorker(QThread):
    """Synthesize speech for a short text off the UI thread via core.tts.

    Signal:
        done(bytes) -- MP3 audio bytes (b'' on failure / unavailable).
    """
    done = Signal(bytes)

    def __init__(self, text, lang, parent=None):
        super().__init__(parent)
        self.text = text
        self.lang = lang

    def run(self):
        from core import tts
        try:
            audio = tts.synthesize(self.text, self.lang)
        except Exception:  # noqa: BLE001 - a failed synthesis just yields no audio
            audio = b""
        self.done.emit(audio or b"")


class _StopRequested(Exception):
    """Raised inside the worker thread when the user asked to stop."""


class TranslationWorker(QThread):
    progress = Signal(float, str)
    finished = Signal(str, list)
    failed = Signal(str)

    def __init__(self, file_path, model, use_online, api_key, src_lang, dst_lang,
                 max_token, max_retries, thread_count, glossary_name,
                 bilingual_flags, session_lang="en", isolation_subdir=None,
                 parent=None, continue_mode=False, resume_dirs=None, run_stamp=None,
                 resume_record_id=None):
        super().__init__(parent)
        self.file_path = file_path
        self.model = model
        self.use_online = use_online
        self.api_key = api_key
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.max_token = max_token
        self.max_retries = max_retries
        self.thread_count = thread_count
        self.glossary_name = glossary_name
        # bilingual_flags: dict of config-key -> bool (e.g. word_bilingual_mode)
        self.bilingual_flags = dict(bilingual_flags or {})
        self.session_lang = session_lang
        # Optional per-file subdir nested under temp/result/log to keep two
        # uploads that share a base name from colliding (file_dir is derived
        # from temp_dir/<basename>). None means use the shared dirs.
        self.isolation_subdir = isolation_subdir
        # Resume: continue_mode reuses any partial temp/result files; resume_dirs
        # (temp, result, log) pins the EXACT dirs of the interrupted run so the
        # already-translated segments are found instead of starting over.
        self.continue_mode = continue_mode
        self.resume_dirs = resume_dirs
        # Per-run subfolder (e.g. "2026-06-17_14-30-05") so each task's outputs
        # land in their own dir instead of all piling into data/result.
        self.run_stamp = run_stamp
        # Resume: reuse the original record's id so the run updates that history
        # row (interrupted -> success) instead of adding a duplicate.
        self.resume_record_id = resume_record_id
        # Stable id for THIS task's history row, known before the run starts so
        # the page can update its status live (e.g. running -> paused). A resume
        # reuses the original row's id; a fresh run gets a new one.
        import uuid as _uuid
        self.translation_id = resume_record_id or _uuid.uuid4().hex
        self._stop = False
        self._paused = False
        # Coverage report for this file (filled after process(); read by the page)
        self.coverage = None

    def request_stop(self):
        self._stop = True
        self._paused = False   # wake a paused run so its blocked threads see the stop

    def request_pause(self):
        self._paused = True

    def request_resume(self):
        self._paused = False

    def is_paused(self):
        return self._paused

    def _check_stop(self):
        # Universal control checkpoint (wired into every backend loop via
        # translator.check_stop_requested). Stop -> raise; Pause -> block in
        # place until resumed/stopped so the thread/process/models stay alive and
        # resume continues from the exact point (true pause, not a restart).
        if self._stop:
            raise _StopRequested()
        while self._paused and not self._stop:
            self.msleep(120)
        if self._stop:
            raise _StopRequested()
        return False

    def run(self):
        try:
            self._translate()
        except _StopRequested:
            self.failed.emit("Translation stopped by user.")
        except HardApiError as e:
            self.failed.emit(self._friendly_api_error(e))
        except Exception as e:  # noqa: BLE001 - surface any backend failure
            self.failed.emit(f"Error: {e}")

    def _translate(self):
        file_extension = os.path.splitext(self.file_path)[1]
        file_stem = os.path.splitext(self.file_path)[0]

        translator_class = backend.get_translator_class(
            file_extension,
            excel_mode_2=self.bilingual_flags.get("excel_bilingual_mode", False),
            word_bilingual_mode=self.bilingual_flags.get("word_bilingual_mode", False),
            excel_bilingual_mode=self.bilingual_flags.get("excel_bilingual_mode", False),
            pdf_bilingual_mode=self.bilingual_flags.get("pdf_bilingual_mode", False),
            subtitle_bilingual_mode=self.bilingual_flags.get("subtitle_bilingual_mode", False),
            txt_bilingual_mode=self.bilingual_flags.get("txt_bilingual_mode", False),
            md_bilingual_mode=self.bilingual_flags.get("md_bilingual_mode", False),
            epub_bilingual_mode=self.bilingual_flags.get("epub_bilingual_mode", False),
            html_bilingual_mode=self.bilingual_flags.get("html_bilingual_mode", False),
        )
        if translator_class is None:
            self.failed.emit(f"Unsupported file type '{file_extension}'.")
            return

        src_code = backend.language_code(self.src_lang)
        dst_code = backend.language_code(self.dst_lang)
        gpath = backend.glossary_path(self.glossary_name) if self.glossary_name else None
        if self.resume_dirs:
            # Resume: reuse the interrupted run's exact dirs (with its temp files).
            temp_dir, result_dir, log_dir = self.resume_dirs
            for d in (temp_dir, result_dir, log_dir):
                os.makedirs(d, exist_ok=True)
        else:
            temp_dir, result_dir, log_dir = backend.get_custom_paths()
            # One subfolder per run (start datetime), so each task's outputs are
            # grouped instead of dumped together; same-name files inside a run
            # still get the isolation_subdir nested below.
            sub = os.path.join(self.run_stamp, self.isolation_subdir) if (
                self.run_stamp and self.isolation_subdir) else (
                self.run_stamp or self.isolation_subdir)
            if sub:
                temp_dir = os.path.join(temp_dir, sub)
                result_dir = os.path.join(result_dir, sub)
                log_dir = os.path.join(log_dir, sub)
                for d in (temp_dir, result_dir, log_dir):
                    os.makedirs(d, exist_ok=True)

        # The per-project log is opened by base_translator.process() into the
        # result folder, bound to this run's context (so concurrent files don't
        # interleave). Nothing to set up here.

        # The history DB must be the ONE global store the History page reads
        # (data/log), NOT the per-run stamped subdir — otherwise records never
        # show up and a resumed run can't update its original row. The per-project
        # .log still lives in the run's result folder (opened by process()).
        _, _, history_dir = backend.get_custom_paths()
        translator = translator_class(
            self.file_path, self.model, self.use_online, self.api_key,
            src_code, dst_code, self.continue_mode,
            max_token=self.max_token, max_retries=self.max_retries,
            thread_count=self.thread_count, glossary_path=gpath,
            temp_dir=temp_dir, result_dir=result_dir,
            session_lang=self.session_lang, log_dir=log_dir,
            history_dir=history_dir,
        )
        translator.check_stop_requested = self._check_stop
        translator.translation_id = self.translation_id
        # Captured into the history record if this run fails/stops, so a later
        # "Continue" can reconstruct THIS exact worker (display langs, glossary,
        # bilingual flags — things the translator itself doesn't keep).
        translator.resume_info = {
            "src_lang": self.src_lang, "dst_lang": self.dst_lang,
            "model": self.model, "use_online": self.use_online,
            "glossary_name": self.glossary_name,
            "bilingual_flags": self.bilingual_flags,
            "session_lang": self.session_lang,
            "temp_dir": temp_dir, "result_dir": result_dir, "log_dir": log_dir,
        }

        def progress_callback(value, desc=None):
            self._check_stop()
            self.progress.emit(float(value), desc or "")

        progress_callback(0.0, "Extracting text...")
        output_path, missing_counts = translator.process(
            file_stem, file_extension, progress_callback=progress_callback
        )

        # Translation coverage (best-effort): base_translator drops coverage.json
        # in the result dir; stash it on the worker for the page to display.
        try:
            cov_path = os.path.join(result_dir, "coverage.json")
            if os.path.exists(cov_path):
                with open(cov_path, "r", encoding="utf-8") as f:
                    self.coverage = json.load(f)
        except Exception:  # noqa: BLE001 — coverage is non-essential
            pass

        total_tokens = getattr(translator, "total_tokens", 0)
        # Stash exact usage for the page's thank-you / cost summary.
        self.total_tokens = total_tokens
        self.prompt_tokens = getattr(translator, "total_prompt_tokens", 0)
        self.completion_tokens = getattr(translator, "total_completion_tokens", 0)
        final_stats = getattr(translator, "final_stats", "")
        desc = "Translation completed"
        if final_stats:
            desc = f"{desc} | {final_stats}"
        elif total_tokens:
            tokens_str = f"{total_tokens / 1000:.1f}K" if total_tokens >= 1000 else str(total_tokens)
            desc = f"{desc} | Total tokens used: {tokens_str}"
        self.progress.emit(1.0, desc)

        # Optionally drop the finished file next to its SOURCE (config toggle),
        # e.g. 1.mp4 -> its subtitle lands in 1.mp4's folder. Only the deliverable
        # is copied; temp/log/coverage stay under data/ to avoid cluttering the
        # user's folder. Resume runs keep their pinned dirs.
        if (output_path and not self.resume_dirs
                and backend.read_config().get("output_beside_source", False)):
            try:
                dest_dir = os.path.dirname(os.path.abspath(self.file_path))
                dest = os.path.join(dest_dir, os.path.basename(output_path))
                if os.path.abspath(dest) != os.path.abspath(output_path):
                    shutil.copy2(output_path, dest)
                output_path = dest
            except Exception as e:  # noqa: BLE001 — never fail the run over a copy
                from core.log_config import app_logger
                app_logger.warning(f"Could not save output beside source: {e}")

        missing = sorted(missing_counts) if missing_counts else []
        self.finished.emit(output_path, missing)

    def _friendly_api_error(self, error):
        """Localized, category-specific message for a fatal API error."""
        from qt_app.i18n import tr
        category = getattr(error, "category", None)
        if category is None:
            category = classify_fatal_error(str(error))
        keys = {
            "insufficient_balance": "Err Insufficient Balance",
            "invalid_key": "Err Invalid Key",
            "server_error": "Err Server",
        }
        return tr(keys.get(category, "Err Api Generic"), self.session_lang)
