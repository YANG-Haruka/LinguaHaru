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
import subprocess

from PySide6.QtCore import QThread, Signal

from llmWrapper.online_translation import HardApiError
from qt_app import backend


class InstallWorker(QThread):
    """Runs ``pip install -r requirements-*.txt`` for an optional module off the
    UI thread, streaming output lines and reporting success/failure.

    Signals:
        line(str)        -- a line of pip output
        finished_ok(bool, str) -- (success, final message)
    """

    line = Signal(str)
    finished_ok = Signal(bool, str)

    def __init__(self, module_name, parent=None):
        super().__init__(parent)
        self.module_name = module_name

    def run(self):
        cmd = backend.install_command_for(self.module_name)
        if not cmd:
            self.finished_ok.emit(False, f"Unknown module: {self.module_name}")
            return
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
            return
        if proc.returncode == 0:
            self.finished_ok.emit(True, "Installation finished")
        else:
            self.finished_ok.emit(False, f"pip exited with code {proc.returncode}")


class _StopRequested(Exception):
    """Raised inside the worker thread when the user asked to stop."""


class TranslationWorker(QThread):
    progress = Signal(float, str)
    finished = Signal(str, list)
    failed = Signal(str)

    def __init__(self, file_path, model, use_online, api_key, src_lang, dst_lang,
                 max_token, max_retries, thread_count, glossary_name,
                 bilingual_flags, session_lang="en", isolation_subdir=None,
                 parent=None):
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
        self._stop = False

    def request_stop(self):
        self._stop = True

    def _check_stop(self):
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
        temp_dir, result_dir, log_dir = backend.get_custom_paths()
        if self.isolation_subdir:
            temp_dir = os.path.join(temp_dir, self.isolation_subdir)
            result_dir = os.path.join(result_dir, self.isolation_subdir)
            log_dir = os.path.join(log_dir, self.isolation_subdir)
            for d in (temp_dir, result_dir, log_dir):
                os.makedirs(d, exist_ok=True)

        from config.log_config import file_logger
        file_logger.create_file_log(os.path.basename(self.file_path), log_dir=log_dir)

        translator = translator_class(
            self.file_path, self.model, self.use_online, self.api_key,
            src_code, dst_code, False,
            max_token=self.max_token, max_retries=self.max_retries,
            thread_count=self.thread_count, glossary_path=gpath,
            temp_dir=temp_dir, result_dir=result_dir,
            session_lang=self.session_lang, log_dir=log_dir,
        )
        translator.check_stop_requested = self._check_stop

        def progress_callback(value, desc=None):
            self._check_stop()
            self.progress.emit(float(value), desc or "")

        progress_callback(0.0, "Extracting text...")
        output_path, missing_counts = translator.process(
            file_stem, file_extension, progress_callback=progress_callback
        )

        total_tokens = getattr(translator, "total_tokens", 0)
        final_stats = getattr(translator, "final_stats", "")
        desc = "Translation completed"
        if final_stats:
            desc = f"{desc} | {final_stats}"
        elif total_tokens:
            tokens_str = f"{total_tokens / 1000:.1f}K" if total_tokens >= 1000 else str(total_tokens)
            desc = f"{desc} | Total tokens used: {tokens_str}"
        self.progress.emit(1.0, desc)

        missing = sorted(missing_counts) if missing_counts else []
        self.finished.emit(output_path, missing)

    @staticmethod
    def _friendly_api_error(error):
        emsg = str(error).lower()
        if "all api keys" in emsg:
            return "All API keys failed (invalid or out of quota). Please replace the key(s)."
        if any(m in emsg for m in ("quota", "insufficient", "balance", "402")):
            return "Insufficient balance/quota. Please top up or switch to another key."
        return "API key is invalid or expired. Please check the API Key in the Translate tab."
