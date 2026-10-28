"""Background workers for real-time voice: model preload, per-utterance
recognition, translation (plain + streamed) and session summaries. Each runs
off the GUI thread and reports back via signals.
"""
import threading

from PySide6.QtCore import QObject, QThread, Signal

# funasr/SenseVoice is not thread-safe — serialize recognition across utterances.
_STT_LOCK = threading.Lock()


class PreloadWorker(QObject):
    """Load the local STT model up front so the first utterance isn't blocked on
    a multi-second model load. Emits progress(download line) while a missing
    model downloads, then done(ready).

    A DAEMON thread, not a QThread: it starts as soon as the page is opened, and
    the app may be closed mid-load — a running QThread would abort Qt at exit
    ('Destroyed while thread is still running'), a daemon thread just dies."""
    progress = Signal(str)
    done = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()

    def isRunning(self):
        return self._t.is_alive()

    def wait(self, ms):
        if self._t.is_alive():
            self._t.join(ms / 1000.0)

    def _run(self):
        try:
            from core.optional_modules import format_download_line
            from core.pipelines.video_translation_pipeline import preload_recognizer

            def _cb(line):
                text = format_download_line(line)
                if text:
                    try:
                        self.progress.emit(text)
                    except RuntimeError:  # page destroyed mid-download
                        pass
            ok = bool(preload_recognizer(progress_cb=_cb))
            if ok:   # final mode previews with a fast model — load it up front too
                from core.pipelines.video_translation_pipeline import preload_live_preview
                preload_live_preview()
        except Exception:  # noqa: BLE001
            ok = False
        try:
            self.done.emit(ok)
        except RuntimeError:  # page already destroyed (app closing)
            pass


class SummaryWorker(QObject):
    """Summarize a saved real-time transcript with the current model and write
    the summary to the top of the transcript file. Daemon thread (like
    PreloadWorker) so a slow LLM call never blocks closing the app. done(ok)."""
    done = Signal(bool)

    def __init__(self, path, out_lang, parent=None):
        super().__init__(parent)
        self._path, self._lang = path, out_lang

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        ok = False
        try:
            from core import backend
            from core.api_keys import load_api_key_for_model
            from core.llm.llm_wrapper import summarize_transcript
            from core.translation_history import add_transcript_summary, read_transcript_body
            online = backend.get_config("default_online", True)
            model = backend.get_active_model(online)
            key = load_api_key_for_model(model) if online else ""
            summary, ok, _u = summarize_transcript(
                read_transcript_body(self._path), self._lang, model, online, key)
            if ok:
                add_transcript_summary(self._path, summary)
        except Exception as e:  # noqa: BLE001
            from core.log_config import app_logger
            app_logger.warning(f"Live summary failed: {e}")
            ok = False
        try:
            self.done.emit(ok)
        except RuntimeError:  # receiver already destroyed (app closing)
            pass


class LiveRecognizeWorker(QThread):
    """Recognize a (possibly partial) PCM16 buffer locally — NO translation.

    Used for streaming captions: the page sends the growing audio of the current
    utterance every ~360ms; we re-run STT and emit the full text-so-far. The page
    applies stable-prefix commit on the text. ``is_final`` marks the end-of-
    utterance pass (flush the remainder)."""
    done = Signal(str, str, bool)   # (text, detected_lang, is_final)

    def __init__(self, pcm_bytes, sample_rate, is_final, parent=None, model_id=None):
        super().__init__(parent)
        self._pcm = pcm_bytes
        self._sr = sample_rate
        self._final = is_final
        self._model_id = model_id     # None = the live-voice model

    def run(self):
        try:
            from core.pipelines.video_translation_pipeline import recognize_utterance
            with _STT_LOCK:   # serialize recognition (one worker at a time)
                text, detected = recognize_utterance(self._pcm, sample_rate=self._sr,
                                                     model_id=self._model_id)
            self.done.emit(text or "", detected or "auto", self._final)
        except Exception:  # noqa: BLE001 — transient; the next partial retries
            self.done.emit("", "auto", self._final)


class LiveTranslateWorker(QThread):
    """Translate one finalized sentence (LLM). Emits done(timestamp, translated)
    so the matching source/translation lines line up by timestamp."""
    done = Signal(str, str, int)   # (timestamp, translated, total_tokens)

    def __init__(self, ts, source, src_code, dst_code, model, use_online,
                 api_key, parent=None, context=""):
        super().__init__(parent)
        self._ts = ts
        self._source = source
        self._src = src_code
        self._dst = dst_code
        self._model = model
        self._online = use_online
        self._key = api_key
        self._context = context

    def run(self):
        try:
            from core import backend
            from core.llm.llm_wrapper import translate_text_simple
            # Glossary terms present in this sentence ride along as context, so
            # proper nouns stay consistent (same terms as document translation).
            hint = backend.live_glossary_hint(self._source, self._src, self._dst)
            ctx = f"{self._context} {hint}".strip() if hint else self._context
            tokens = 0
            # One retry: fast speech fires many requests at once and the odd one
            # hits a rate limit / timeout — that sentence used to vanish silently.
            for attempt in range(2):
                translated, ok, usage = translate_text_simple(
                    self._source, self._src or "auto", self._dst, self._model,
                    self._online, self._key, context=ctx, speech=True)
                tokens += int((usage or {}).get("total_tokens", 0) or 0)
                # Much longer than its source -> the model translated the context
                # too; redo this one without context.
                if ok and ctx and len(translated or "") > max(60, 5 * len(self._source)):
                    ctx = ""
                    continue
                if ok or attempt:
                    break
                self.msleep(1500)
            self.done.emit(self._ts, translated if ok else "", tokens)
        except Exception:  # noqa: BLE001
            self.done.emit(self._ts, "", 0)


class LiveTranslateStreamWorker(QThread):
    """Translate one sentence with token streaming — emits the cumulative text on
    each delta (chunk) so the caption grows live. Online only; offline/failure
    emits once. Used when 'Stream Translation' is enabled."""
    chunk = Signal(str, str)   # (timestamp, cumulative_text)
    done = Signal(str, int)    # (timestamp, total_tokens)

    def __init__(self, ts, source, src_code, dst_code, model, use_online,
                 api_key, parent=None, context=""):
        super().__init__(parent)
        self._ts = ts
        self._source = source
        self._src = src_code
        self._dst = dst_code
        self._model = model
        self._online = use_online
        self._key = api_key
        self._context = context

    def run(self):
        sink = {}
        try:
            from core import backend
            from core.llm.llm_wrapper import translate_text_simple_stream
            hint = backend.live_glossary_hint(self._source, self._src, self._dst)
            ctx = f"{self._context} {hint}".strip() if hint else self._context
            for partial in translate_text_simple_stream(
                    self._source, self._src or "auto", self._dst, self._model,
                    self._online, self._key, usage_sink=sink, context=ctx, speech=True):
                self.chunk.emit(self._ts, partial or "")
        except Exception:  # noqa: BLE001
            pass
        self.done.emit(self._ts, int(sink.get("total_tokens", 0) or 0))
