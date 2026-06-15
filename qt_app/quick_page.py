"""Quick Translate page: a Google-Translate-style short-text translator.

This is the app's PRIMARY "翻译" page (the file-based view is "文件翻译"). The
user types (or speaks) a short text, picks source/target languages, and gets an
instant translation via the ACTIVE interface (same model resolution as
document/voice translation). Recent translations are remembered (<=50) and
click-to-reload.

Text translation always works (no optional deps). Voice input AND read-aloud
both need the "翻译语音输入" plugin (STT + edge-tts); when it is unavailable both
the mic and speaker buttons are disabled with a hint and a one-click jump to the
Plugins page (mirrors live_page). Voice input uses the QUICK STT model.
"""

import os
import tempfile

from PySide6.QtCore import Qt, QEvent, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QListWidgetItem,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel, ComboBox,
    CardWidget, PlainTextEdit, TextEdit, PushButton, PrimaryPushButton,
    ToolButton, TransparentToolButton, FluentIcon, InfoBar, InfoBarPosition,
    ListWidget,
)

from core import backend
from core import quick_translate
from qt_app.i18n import tr
from qt_app.worker import QuickTranslateWorker, TtsWorker

_IN_RATE = 16000  # SenseVoice / mic input sample rate


class QuickPage(ScrollArea):
    def __init__(self, parent=None, lang="zh"):
        super().__init__(parent)
        self.setObjectName("QuickPage")
        self._lang = lang
        self._worker = None
        self._tts = None
        self._player = None
        self._audio_out = None
        self._tts_path = None
        self.on_open_plugins = None  # set by MainWindow -> jump to Plugins page

        # voice capture state (reuses live_page's QAudioSource approach)
        self._source = None
        self._mic_io = None
        self._in_fmt = None
        self._rec_buf = bytearray()

        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        container = QWidget()
        container.setObjectName("quickScrollContainer")
        container.setStyleSheet(
            "#quickScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(14)

        # --- page head ---
        self.title = TitleLabel(tr("Translate", lang))
        layout.addWidget(self.title)
        self.subtitle = CaptionLabel(tr("Quick Translate Subtitle", lang))
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        # --- language bar: source [swap] target ---
        config = backend.read_config()
        langs = backend.available_languages()
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        self.src_combo = ComboBox()
        self.src_combo.addItem(tr("Auto Detect", lang), userData="auto")
        for name in langs:
            self.src_combo.addItem(name, userData=name)
        self.swap_btn = ToolButton(FluentIcon.ROTATE)
        self.swap_btn.clicked.connect(self.on_swap)
        self.dst_combo = ComboBox()
        self.dst_combo.addItems(langs)
        default_dst = config.get("default_dst_lang", "中文")
        self._set_combo(self.dst_combo, default_dst if self.dst_combo.findText(default_dst) >= 0 else "中文")
        lang_row.addWidget(self.src_combo, 1)
        lang_row.addWidget(self.swap_btn)
        lang_row.addWidget(self.dst_combo, 1)
        layout.addLayout(lang_row)

        # --- two panes: input (left) / output (right) ---
        panes = QHBoxLayout()
        panes.setSpacing(14)

        # left pane
        left_card = CardWidget()
        left = QVBoxLayout(left_card)
        left.setContentsMargins(16, 14, 16, 14)
        left.setSpacing(8)
        self.input_text = PlainTextEdit()
        self.input_text.setPlaceholderText(tr("Enter Text", lang))
        self.input_text.setMinimumHeight(220)
        self.input_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.input_text.installEventFilter(self)
        left.addWidget(self.input_text, 1)
        left_bottom = QHBoxLayout()
        self.input_hint = CaptionLabel(tr("Enter To Translate", lang))
        self.input_hint.setTextColor("#606060", "#a0a0a0")
        left_bottom.addWidget(self.input_hint, 1)
        self.mic_btn = ToolButton(FluentIcon.MICROPHONE)
        self.mic_btn.setToolTip(tr("Voice Input", lang))
        self.mic_btn.clicked.connect(self.on_mic)
        left_bottom.addWidget(self.mic_btn)
        left.addLayout(left_bottom)
        panes.addWidget(left_card, 1)

        # right pane
        right_card = CardWidget()
        right = QVBoxLayout(right_card)
        right.setContentsMargins(16, 14, 16, 14)
        right.setSpacing(8)
        self.output_text = TextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(220)
        self.output_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.output_text, 1)
        right_bottom = QHBoxLayout()
        right_bottom.addStretch(1)
        self.speak_btn = ToolButton(FluentIcon.VOLUME)
        self.speak_btn.setToolTip(tr("Read Aloud", lang))
        self.speak_btn.clicked.connect(self.on_read_aloud)
        right_bottom.addWidget(self.speak_btn)
        self.copy_btn = PushButton(FluentIcon.COPY, tr("Copy", lang))
        self.copy_btn.clicked.connect(self.on_copy)
        right_bottom.addWidget(self.copy_btn)
        right.addLayout(right_bottom)
        panes.addWidget(right_card, 1)

        # Give the panes the lion's share of the vertical space so the boxes are
        # as large as the layout allows (history is collapsed by default).
        layout.addLayout(panes, 1)

        # --- voice hint + jump-to-plugins (shown only when voice unavailable) ---
        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        self.voice_hint = CaptionLabel("")
        self.voice_hint.setWordWrap(True)
        hint_row.addWidget(self.voice_hint, 1)
        self.plugin_btn = PushButton(FluentIcon.APPLICATION, tr("Go to Plugins", lang))
        self.plugin_btn.clicked.connect(lambda: self.on_open_plugins and self.on_open_plugins())
        self.plugin_btn.hide()
        hint_row.addWidget(self.plugin_btn)
        layout.addLayout(hint_row)

        # --- collapsible history ---
        # Header: a clickable chevron + "History" label toggles the full list.
        hist_head = QHBoxLayout()
        hist_head.setSpacing(6)
        self.hist_toggle_btn = TransparentToolButton(FluentIcon.CHEVRON_RIGHT)
        self.hist_toggle_btn.clicked.connect(self._toggle_history)
        hist_head.addWidget(self.hist_toggle_btn)
        self.history_title = StrongBodyLabel(tr("History", lang))
        self.history_title.installEventFilter(self)  # click the label to toggle too
        hist_head.addWidget(self.history_title)
        hist_head.addStretch(1)
        layout.addLayout(hist_head)

        # Collapsed view: just the single most-recent entry (click to reload).
        self.latest_label = BodyLabel("")
        self.latest_label.setWordWrap(True)
        self.latest_label.setCursor(Qt.PointingHandCursor)
        self.latest_label.installEventFilter(self)
        layout.addWidget(self.latest_label)

        # Expanded view: the full list (up to 50) + a Clear button. Hidden until
        # the user expands the section.
        self.history_list = ListWidget()
        self.history_list.setMinimumHeight(160)
        self.history_list.itemClicked.connect(self._on_history_clicked)
        self.history_list.hide()
        layout.addWidget(self.history_list)

        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        self.clear_btn = PushButton(FluentIcon.DELETE, tr("Clear History", lang))
        self.clear_btn.clicked.connect(self.on_clear_history)
        clear_row.addWidget(self.clear_btn)
        self.clear_row_host = QWidget()
        self.clear_row_host.setLayout(clear_row)
        self.clear_row_host.hide()
        layout.addWidget(self.clear_row_host)

        self._history_expanded = False
        self._update_voice_availability()
        self.reload_history()

    # --- i18n ---
    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Translate", lang))
        self.subtitle.setText(tr("Quick Translate Subtitle", lang))
        self.src_combo.setItemText(0, tr("Auto Detect", lang))
        self.input_text.setPlaceholderText(tr("Enter Text", lang))
        self.input_hint.setText(tr("Enter To Translate", lang))
        self.mic_btn.setToolTip(tr("Voice Input", lang))
        self.speak_btn.setToolTip(tr("Read Aloud", lang))
        self.copy_btn.setText(tr("Copy", lang))
        self.plugin_btn.setText(tr("Go to Plugins", lang))
        self.history_title.setText(tr("History", lang))
        self.clear_btn.setText(tr("Clear History", lang))
        self._update_voice_availability()
        self.reload_history()

    # --- helpers ---
    @staticmethod
    def _set_combo(combo, value):
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _src_value(self):
        """Display name or 'auto' for the source combo (carried as item data)."""
        data = self.src_combo.currentData()
        return data if data is not None else self.src_combo.currentText()

    def showEvent(self, event):
        # Refresh history (it may have changed via the Web frontend or another
        # session) and voice availability each time the page is shown.
        self.reload_history()
        self._update_voice_availability()
        super().showEvent(event)

    def hideEvent(self, event):
        # Leaving the page must not keep the mic hot or audio playing.
        if self._source is not None:
            self._stop_recording(dispatch=False)
        self._stop_playback()
        super().hideEvent(event)

    # --- Enter-to-translate + click-to-toggle/reload via event filter ---
    def eventFilter(self, obj, event):
        # ScrollArea's base __init__ installs filters before our widgets exist.
        if obj is getattr(self, "input_text", None) and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    return False  # Shift+Enter -> newline (default behavior)
                self.on_translate()
                return True       # Enter -> translate (swallow the newline)
        # Click the section header label to expand/collapse.
        if obj is getattr(self, "history_title", None) and event.type() == QEvent.MouseButtonRelease:
            self._toggle_history()
            return True
        # Click the collapsed "latest" entry to reload it.
        if obj is getattr(self, "latest_label", None) and event.type() == QEvent.MouseButtonRelease:
            items = quick_translate.get_history()
            if items:
                self._load_entry(items[0])
            return True
        return super().eventFilter(obj, event)

    # --- translate ---
    def on_translate(self):
        if self._worker is not None and self._worker.isRunning():
            return
        text = self.input_text.toPlainText().strip()
        if not text:
            return
        self._set_busy(True)
        self.output_text.setPlainText(tr("Translating", self._lang) + "...")
        self._worker = QuickTranslateWorker(
            text, self._src_value(), self.dst_combo.currentText())
        self._worker.done.connect(self._on_translated)
        self._worker.start()

    def _on_translated(self, translated, ok):
        self._set_busy(False)
        if not ok or not translated:
            self.output_text.setPlainText("")
            self._info(tr("Translation failed", self._lang), error=True)
            return
        self.output_text.setPlainText(translated)
        src = self.input_text.toPlainText().strip()
        quick_translate.add_history(
            src, translated, self._src_value(), self.dst_combo.currentText())
        self.reload_history()

    def _set_busy(self, busy):
        self.input_text.setEnabled(not busy)

    def on_swap(self):
        """Swap source <-> target. 'auto' source becomes the target's language;
        the new source is set to the old target's display name."""
        src_name = self._src_value()
        dst_name = self.dst_combo.currentText()
        # New target = old source (skip if it was 'auto').
        if src_name != "auto" and self.dst_combo.findText(src_name) >= 0:
            self._set_combo(self.dst_combo, src_name)
        # New source = old target.
        idx = self.src_combo.findText(dst_name)
        if idx >= 0:
            self.src_combo.setCurrentIndex(idx)

    # --- copy ---
    def on_copy(self):
        text = self.output_text.toPlainText().strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self._info(tr("Copied", self._lang), error=False)

    # --- read aloud (TTS off the UI thread, then play the mp3) ---
    def on_read_aloud(self):
        from core.optional_modules import quick_voice_available
        if not quick_voice_available():
            self._info(tr("Voice Needs Plugin", self._lang), error=True)
            return
        text = self.output_text.toPlainText().strip()
        if not text:
            return
        if self._tts is not None and self._tts.isRunning():
            return
        self.speak_btn.setEnabled(False)
        # Synthesize the OUTPUT text in the TARGET language.
        self._tts = TtsWorker(text, self.dst_combo.currentText())
        self._tts.done.connect(self._on_tts_done)
        self._tts.start()

    def _on_tts_done(self, audio):
        self.speak_btn.setEnabled(True)
        if not audio:
            self._info(tr("Voice Needs Plugin", self._lang), error=True)
            return
        self._play_mp3(audio)

    def _play_mp3(self, audio):
        """Write the mp3 bytes to a temp file and play via Qt's multimedia
        backend (ffmpeg). The player/output are kept alive on self."""
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        except Exception as e:  # noqa: BLE001
            self._info(f"QtMultimedia unavailable: {e}", error=True)
            return
        self._stop_playback()
        try:
            fd, path = tempfile.mkstemp(suffix=".mp3", prefix="lh_tts_")
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
        except Exception as e:  # noqa: BLE001
            self._info(f"Audio write failed: {e}", error=True)
            return
        self._tts_path = path
        self._audio_out = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_out)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _stop_playback(self):
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001
                pass
            self._player = None
            self._audio_out = None
        if self._tts_path and os.path.exists(self._tts_path):
            try:
                os.remove(self._tts_path)
            except Exception:  # noqa: BLE001 - temp file removal is best-effort
                pass
        self._tts_path = None

    # --- history (collapsible) ---
    def _toggle_history(self):
        self._history_expanded = not self._history_expanded
        self._apply_history_visibility()

    def _apply_history_visibility(self):
        expanded = self._history_expanded
        self.hist_toggle_btn.setIcon(
            FluentIcon.CHEVRON_DOWN_MED if expanded else FluentIcon.CHEVRON_RIGHT_MED)
        self.history_list.setVisible(expanded)
        self.clear_row_host.setVisible(expanded)
        # In the collapsed state show only the single most-recent entry.
        self.latest_label.setVisible(not expanded)

    def reload_history(self):
        items = quick_translate.get_history()
        # Collapsed view: the single most-recent entry.
        self.latest_label.setText(self._entry_text(items[0]) if items else "")
        # Expanded view: the full list.
        self.history_list.clear()
        for it in items:
            item = QListWidgetItem(self._entry_text(it))
            item.setData(Qt.UserRole, it)
            self.history_list.addItem(item)
        self._apply_history_visibility()

    @staticmethod
    def _entry_text(it):
        src = (it.get("src") or "").replace("\n", " ")
        tgt = (it.get("translated") or "").replace("\n", " ")
        text = f"{src}  →  {tgt}"
        return text[:89] + "…" if len(text) > 90 else text

    def _on_history_clicked(self, item):
        self._load_entry(item.data(Qt.UserRole) or {})

    def _load_entry(self, it):
        # Restore languages.
        src_lang = it.get("src_lang", "auto")
        idx = self.src_combo.findData(src_lang)
        if idx < 0:
            idx = self.src_combo.findText(src_lang)
        if idx >= 0:
            self.src_combo.setCurrentIndex(idx)
        self._set_combo(self.dst_combo, it.get("dst_lang", ""))
        # Restore the text + its translation.
        self.input_text.setPlainText(it.get("src", ""))
        self.output_text.setPlainText(it.get("translated", ""))

    def on_clear_history(self):
        quick_translate.clear_history()
        self.reload_history()

    # --- voice input (reuses live_page's QAudioSource + recognize_utterance) ---
    def _update_voice_availability(self):
        from core.optional_modules import quick_voice_available
        available = quick_voice_available()
        # Gate BOTH the mic and the read-aloud speaker on the single plugin.
        self.mic_btn.setEnabled(available or self._source is not None)
        self.speak_btn.setEnabled(available)
        if available:
            self.mic_btn.setToolTip(tr("Voice Input", self._lang))
            self.speak_btn.setToolTip(tr("Read Aloud", self._lang))
            self.voice_hint.setText("")
            self.plugin_btn.hide()
        else:
            self.mic_btn.setToolTip(tr("Voice Needs Plugin", self._lang))
            self.speak_btn.setToolTip(tr("Voice Needs Plugin", self._lang))
            self.voice_hint.setText(tr("Voice Needs Plugin", self._lang))
            self.plugin_btn.show()

    def on_mic(self):
        if self._source is not None:
            self._stop_recording(dispatch=True)
        else:
            self._start_recording()

    def _start_recording(self):
        from core.optional_modules import quick_voice_available
        if not quick_voice_available():
            self._info(tr("Voice Needs Plugin", self._lang), error=True)
            return
        try:
            from PySide6.QtMultimedia import (
                QAudioSource, QAudioFormat, QMediaDevices)
        except Exception as e:  # noqa: BLE001
            self._info(f"QtMultimedia unavailable: {e}", error=True)
            return
        in_dev = QMediaDevices.defaultAudioInput()
        if in_dev is None or in_dev.isNull():
            self._info(tr("No microphone found", self._lang), error=True)
            return
        want = QAudioFormat()
        want.setSampleRate(_IN_RATE)
        want.setChannelCount(1)
        want.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._in_fmt = want if in_dev.isFormatSupported(want) else in_dev.preferredFormat()
        self._source = QAudioSource(in_dev, self._in_fmt)
        self._mic_io = self._source.start()
        if self._mic_io is None:
            self._info(tr("No microphone found", self._lang), error=True)
            self._source = None
            return
        self._rec_buf = bytearray()
        self._mic_io.readyRead.connect(self._on_mic_ready)
        self.mic_btn.setIcon(FluentIcon.PAUSE)
        self.input_hint.setText(tr("Listening", self._lang) + "...")

    def _on_mic_ready(self):
        if self._mic_io is None:
            return
        data = bytes(self._mic_io.readAll().data())
        if not data:
            return
        from PySide6.QtMultimedia import QAudioFormat
        fmt = self._in_fmt
        if (fmt.sampleRate() == _IN_RATE and fmt.channelCount() == 1
                and fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16):
            pcm = data
        else:
            # Reuse live_page's pure-Python converters for odd device formats.
            from qt_app.live_page import (
                _decode_to_mono_float, _resample, _encode_from_mono_float)
            samples = _decode_to_mono_float(data, fmt.sampleFormat(), fmt.channelCount())
            samples = _resample(samples, fmt.sampleRate(), _IN_RATE)
            pcm = _encode_from_mono_float(samples, QAudioFormat.SampleFormat.Int16, 1)
        self._rec_buf += pcm

    def _stop_recording(self, dispatch):
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._mic_io = None
        self.mic_btn.setIcon(FluentIcon.MICROPHONE)
        self.input_hint.setText(tr("Enter To Translate", self._lang))
        utt = bytes(self._rec_buf)
        self._rec_buf = bytearray()
        if dispatch and utt:
            self._recognize(utt)

    def _recognize(self, utt):
        self.input_hint.setText(tr("Recognizing", self._lang) + "...")
        self._stt = _RecognizeWorker(utt, _IN_RATE)
        self._stt.done.connect(self._on_recognized)
        self._stt.start()

    def _on_recognized(self, text):
        self.input_hint.setText(tr("Enter To Translate", self._lang))
        if not text:
            return
        self.input_text.setPlainText(text)
        self.on_translate()  # auto-translate the recognized text

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Translate", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=2500, parent=self)


from PySide6.QtCore import QThread, Signal


class _RecognizeWorker(QThread):
    """Recognize one captured utterance off the UI thread (STT is blocking and
    funasr is not thread-safe; serialized via live_worker._STT_LOCK). Uses the
    QUICK STT model selection."""
    done = Signal(str)

    def __init__(self, pcm_bytes, sample_rate, parent=None):
        super().__init__(parent)
        self._pcm = pcm_bytes
        self._sr = sample_rate

    def run(self):
        try:
            from core.pipelines.video_translation_pipeline import (
                recognize_utterance, get_selected_quick_stt_model)
            from qt_app.live_worker import _STT_LOCK
            with _STT_LOCK:
                text, _detected = recognize_utterance(
                    self._pcm, sample_rate=self._sr,
                    model_id=get_selected_quick_stt_model())
            self.done.emit(text or "")
        except Exception:  # noqa: BLE001 - a failed recognition just yields nothing
            self.done.emit("")
