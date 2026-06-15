"""Real-time voice translation page (Gemini 3.5 Live Translate).

Mirrors the Web "实时语音" tab, but the desktop app talks to Gemini directly via
LiveWorker (the Google key is local, so no proxy is needed). The mic is captured
with QAudioSource and the translated speech is played through QAudioSink.

Gemini Live needs 16 kHz mono PCM16 in and emits 24 kHz mono PCM16 out. Real
audio devices rarely offer exactly that, and numpy isn't a base dependency, so
the small pure-Python helpers below decode/resample/re-encode with the stdlib
``array`` module. Transcripts arrive as incremental fragments and are appended.
"""

import array

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QPushButton,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel, ComboBox,
    CardWidget, TextEdit, FluentIcon, InfoBar,
    InfoBarPosition, ProgressBar, IconWidget,
)

from core import backend
from qt_app.i18n import tr
from qt_app.live_worker import LiveWorker, LocalLiveWorker, PreloadWorker
from core.api_keys import load_api_key_for_model
from core.languages_config import LANGUAGE_MAP
from core.optional_modules import video_translation_available

_GOOGLE_PROVIDER = "(Google) Live Translate"
_IN_RATE = 16000   # Gemini input / SenseVoice input
_OUT_RATE = 24000  # Gemini output

# Energy-VAD thresholds for the local mode (mirror the Web vad-worklet).
# Lowered so a soft voice still trips onset (the adaptive floor guards noise).
_VAD_ON_ABS, _VAD_ON_MUL = 0.006, 2.2
_VAD_OFF_ABS, _VAD_OFF_MUL = 0.004, 1.6
_VAD_ON_MS, _VAD_HANG_MS = 90, 600
_VAD_MIN_MS, _VAD_MAX_MS = 280, 30000
# Lead-in kept before speech onset is confirmed, so the first words (often the
# key info) aren't clipped. Mirrors the Web vad-worklet's pre-roll ring buffer.
_VAD_PREROLL_MS = 500


# --------------------------------------------------------------------------- #
# Pure-Python PCM conversion (no numpy). Samples are mono floats in [-1, 1].
# --------------------------------------------------------------------------- #
def _decode_to_mono_float(data, sample_format, channels):
    """Decode interleaved PCM ``data`` to a list of mono float samples."""
    from PySide6.QtMultimedia import QAudioFormat
    SF = QAudioFormat.SampleFormat
    if sample_format == SF.Int16:
        a = array.array("h"); a.frombytes(data); scale = 32768.0
        samples = [v / scale for v in a]
    elif sample_format == SF.Int32:
        a = array.array("i"); a.frombytes(data); scale = 2147483648.0
        samples = [v / scale for v in a]
    elif sample_format == SF.UInt8:
        a = array.array("B"); a.frombytes(data)
        samples = [(v - 128) / 128.0 for v in a]
    elif sample_format == SF.Float:
        a = array.array("f"); a.frombytes(data)
        samples = list(a)
    else:
        return []
    if channels > 1:
        samples = [sum(samples[i:i + channels]) / channels
                   for i in range(0, len(samples) - channels + 1, channels)]
    return samples


def _resample(samples, in_rate, out_rate):
    """Linear-interpolate mono ``samples`` from ``in_rate`` to ``out_rate``."""
    if in_rate == out_rate or not samples:
        return samples
    ratio = out_rate / in_rate
    n = int(len(samples) * ratio)
    last = len(samples) - 1
    out = [0.0] * n
    for i in range(n):
        pos = i / ratio
        i0 = int(pos)
        s0 = samples[i0]
        s1 = samples[i0 + 1] if i0 < last else s0
        out[i] = s0 + (s1 - s0) * (pos - i0)
    return out


def _encode_from_mono_float(samples, sample_format, channels):
    """Encode mono float ``samples`` to interleaved PCM bytes for the format."""
    from PySide6.QtMultimedia import QAudioFormat
    SF = QAudioFormat.SampleFormat
    clamped = [max(-1.0, min(1.0, s)) for s in samples]
    if channels > 1:
        clamped = [s for s in clamped for _ in range(channels)]
    if sample_format == SF.Int16:
        return array.array("h", [int(s * 32767) for s in clamped]).tobytes()
    if sample_format == SF.Int32:
        return array.array("i", [int(s * 2147483647) for s in clamped]).tobytes()
    if sample_format == SF.UInt8:
        return array.array("B", [int(s * 127) + 128 for s in clamped]).tobytes()
    if sample_format == SF.Float:
        return array.array("f", clamped).tobytes()
    return b""


class LivePage(ScrollArea):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("LivePage")
        self._lang = lang
        self._mode = "local"    # "local" (SenseVoice+LLM) | "google" (Gemini Live)
        self._worker = None
        self._preloader = None
        self._local_workers = []
        self._source = None     # QAudioSource (mic)
        self._mic_io = None
        self._sink = None       # QAudioSink (playback)
        self._play_io = None
        self._in_fmt = None
        self._out_fmt = None
        # local-mode energy VAD state
        self._vad_on = False
        self._vad_buf = bytearray()
        self._vad_preroll = bytearray()
        self._vad_voice_ms = 0.0
        self._vad_sil_ms = 0.0
        self._vad_floor = 0.003

        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        container = QWidget()
        container.setObjectName("liveScrollContainer")
        container.setStyleSheet(
            "#liveScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(14)

        self.title = TitleLabel(tr("Real-Time Voice", lang))
        layout.addWidget(self.title)
        self.subtitle = CaptionLabel(tr("Real-Time Voice Subtitle", lang))
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        # --- Controls: mode + target language + start/stop, all on one row ---
        self._mode_ids = ["local", "google"]
        ctrl_card = CardWidget()
        ctrl = QHBoxLayout(ctrl_card)
        ctrl.setContentsMargins(20, 14, 20, 14)
        ctrl.setSpacing(10)
        self.mode_label = BodyLabel(tr("Live Mode", lang))
        ctrl.addWidget(self.mode_label)
        self.mode_combo = ComboBox()
        self.mode_combo.addItems([tr("Local Voice Mode", lang), tr("Google Voice Mode", lang)])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl.addWidget(self.mode_combo, 1)
        self.target_label = BodyLabel(tr("Target Language", lang))
        ctrl.addWidget(self.target_label)
        self.target_combo = ComboBox()
        self.target_combo.addItems(backend.available_languages())
        config = backend.read_config()
        self._set_combo(self.target_combo, config.get("default_dst_lang", "English"))
        ctrl.addWidget(self.target_combo, 1)
        self.mic_label = BodyLabel(tr("Microphone", lang))
        ctrl.addWidget(self.mic_label)
        self.mic_combo = ComboBox()
        self._mic_devices = []
        self._populate_mics()
        ctrl.addWidget(self.mic_combo, 1)
        # Single round start/stop button (green play -> red stop), like the
        # Transync reference.
        self.go_btn = QPushButton("▶")
        self.go_btn.setFixedSize(46, 46)
        self.go_btn.clicked.connect(self._toggle_listen)
        self._style_go(False)
        ctrl.addWidget(self.go_btn)
        layout.addWidget(ctrl_card)

        self.hint_label = CaptionLabel("")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        # Mic level meter: shows you're actually being heard (and loud enough).
        mic_row = QHBoxLayout()
        mic_row.setSpacing(10)
        self.mic_icon = IconWidget(FluentIcon.MICROPHONE, self)
        self.mic_icon.setFixedSize(18, 18)
        mic_row.addWidget(self.mic_icon)
        self.level_bar = ProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setFixedHeight(8)
        self.level_bar.setMaximumWidth(340)
        mic_row.addWidget(self.level_bar)
        mic_row.addStretch(1)
        layout.addLayout(mic_row)

        self.status_label = CaptionLabel("")
        layout.addWidget(self.status_label)

        # --- Transcript panels: source on the LEFT, translation on the RIGHT ---
        panels = QHBoxLayout()
        panels.setSpacing(14)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        self.input_header = StrongBodyLabel(tr("Recognized Speech", lang))
        left_col.addWidget(self.input_header)
        self.input_sub = CaptionLabel(tr("Auto Detect", lang))
        left_col.addWidget(self.input_sub)
        self.input_text = TextEdit()
        self.input_text.setReadOnly(True)
        self.input_text.setMinimumHeight(140)
        self.input_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_col.addWidget(self.input_text, 1)
        panels.addLayout(left_col, 1)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        self.output_header = StrongBodyLabel(tr("Translation Result", lang))
        right_col.addWidget(self.output_header)
        self.model_sub = CaptionLabel("")
        right_col.addWidget(self.model_sub)
        self._refresh_model_sub()
        self.output_text = TextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(140)
        self.output_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_col.addWidget(self.output_text, 1)
        panels.addLayout(right_col, 1)

        # stretch=1 so the panels (and their text boxes) grow to fill the window
        # height; no trailing addStretch (which would pin them to a fixed size).
        layout.addLayout(panels, 1)
        self._update_hint()

    # --- i18n ---
    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Real-Time Voice", lang))
        self.subtitle.setText(tr("Real-Time Voice Subtitle", lang))
        self.mode_label.setText(tr("Live Mode", lang))
        cur = self.mode_combo.currentIndex()
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        self.mode_combo.addItems([tr("Local Voice Mode", lang), tr("Google Voice Mode", lang)])
        self.mode_combo.setCurrentIndex(max(0, cur))
        self.mode_combo.blockSignals(False)
        self.target_label.setText(tr("Target Language", lang))
        self.mic_label.setText(tr("Microphone", lang))
        self._populate_mics()
        self.input_header.setText(tr("Recognized Speech", lang))
        self.input_sub.setText(tr("Auto Detect", lang))
        self.output_header.setText(tr("Translation Result", lang))
        self._refresh_model_sub()
        self._update_hint()

    def _on_mode_changed(self, index):
        if 0 <= index < len(self._mode_ids):
            self._mode = self._mode_ids[index]
        self._update_hint()

    def _update_hint(self):
        """Show a hint when the chosen mode isn't ready (no plugin / no key)."""
        msg = ""
        if self._mode == "local":
            if not video_translation_available():
                msg = tr("Local Voice Needs Plugin", self._lang)
        else:
            if not load_api_key_for_model(_GOOGLE_PROVIDER):
                msg = tr("Google key not set", self._lang)
        self.hint_label.setText(msg)

    @staticmethod
    def _set_combo(combo, value):
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _populate_mics(self):
        """List available microphones (index 0 = system default). Refreshed when
        the page is shown, so newly plugged devices appear."""
        try:
            from PySide6.QtMultimedia import QMediaDevices
        except Exception:  # noqa: BLE001
            return
        prev = self.mic_combo.currentText() if self.mic_combo.count() else ""
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self._mic_devices = list(QMediaDevices.audioInputs())
        self.mic_combo.addItem(tr("Default Microphone", self._lang))
        for dev in self._mic_devices:
            self.mic_combo.addItem(dev.description())
        idx = self.mic_combo.findText(prev)
        self.mic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mic_combo.blockSignals(False)

    def _refresh_model_sub(self):
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        self.model_sub.setText(f"{tr('Current Model', self._lang)}: {model or '-'}")

    def _selected_mic(self):
        """The chosen QAudioDevice, or None to mean the system default."""
        i = self.mic_combo.currentIndex()
        if i > 0 and (i - 1) < len(self._mic_devices):
            return self._mic_devices[i - 1]
        return None

    def showEvent(self, event):
        # Refresh the mic list + active model each time the page is shown.
        if self._source is None:
            self._populate_mics()
        self._refresh_model_sub()
        super().showEvent(event)

    # --- lifecycle ---
    def _style_go(self, running):
        """Round green 'play' button; turns red 'stop' while listening."""
        self.go_btn.setText("■" if running else "▶")
        color, hover = ("#ef4444", "#dc2626") if running else ("#22c55e", "#16a34a")
        self.go_btn.setStyleSheet(
            "QPushButton{background:%s;color:white;border:none;border-radius:23px;"
            "font-size:17px;font-weight:600;}"
            "QPushButton:hover{background:%s;}" % (color, hover))

    def _toggle_listen(self):
        if self._source is not None:
            self.on_stop()
        else:
            self.on_start()

    def on_start(self):
        if self._source is not None:
            return
        if self._mode == "google" and not load_api_key_for_model(_GOOGLE_PROVIDER):
            self._info(tr("Google key not set", self._lang), error=True)
            return
        if self._mode == "local" and not video_translation_available():
            self._info(tr("Local Voice Needs Plugin", self._lang), error=True)
            return

        self.input_text.clear()
        self.output_text.clear()
        self._reset_vad()

        # Google mode needs playback (it returns spoken audio); local mode is
        # text-only, so skip the speaker.
        if not self._start_audio(with_playback=(self._mode == "google")):
            return

        if self._mode == "google":
            target = LANGUAGE_MAP.get(self.target_combo.currentText(), "en")
            self._worker = LiveWorker(load_api_key_for_model(_GOOGLE_PROVIDER), target)
            self._worker.inputText.connect(self._append_input)
            self._worker.outputText.connect(self._append_output)
            self._worker.audio.connect(self._play_audio)
            self._worker.status.connect(self._on_status)
            self._worker.start()
        else:
            # Local mode: preload the model first and show a loading hint, so the
            # first sentence isn't silently blocked on a slow model load.
            from core.pipelines.video_translation_pipeline import recognizer_ready
            if recognizer_ready():
                self.status_label.setText(tr("Listening", self._lang))
            else:
                self.status_label.setText(tr("Loading model", self._lang))
                self._preloader = PreloadWorker(self)
                self._preloader.done.connect(self._on_preload_done)
                self._preloader.start()

        self._style_go(True)
        self.target_combo.setEnabled(False)
        self.mode_combo.setEnabled(False)

    def _start_audio(self, with_playback=True):
        """Open the mic (QAudioSource); also the speaker (QAudioSink) if needed."""
        try:
            from PySide6.QtMultimedia import (
                QAudioSource, QAudioSink, QAudioFormat, QMediaDevices)
        except Exception as e:  # noqa: BLE001
            self._info(f"QtMultimedia unavailable: {e}", error=True)
            return False

        def _fmt(rate):
            f = QAudioFormat()
            f.setSampleRate(rate)
            f.setChannelCount(1)
            f.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            return f

        in_dev = self._selected_mic() or QMediaDevices.defaultAudioInput()
        if in_dev is None or in_dev.isNull():
            self._info(tr("No microphone found", self._lang), error=True)
            return False

        want_in = _fmt(_IN_RATE)
        self._in_fmt = want_in if in_dev.isFormatSupported(want_in) else in_dev.preferredFormat()
        self._source = QAudioSource(in_dev, self._in_fmt)
        self._mic_io = self._source.start()
        if self._mic_io is None:
            self._info(tr("No microphone found", self._lang), error=True)
            self._source = None
            return False
        self._mic_io.readyRead.connect(self._on_mic_ready)

        if with_playback:
            out_dev = QMediaDevices.defaultAudioOutput()
            want_out = _fmt(_OUT_RATE)
            self._out_fmt = want_out if out_dev.isFormatSupported(want_out) else out_dev.preferredFormat()
            self._sink = QAudioSink(out_dev, self._out_fmt)
            self._play_io = self._sink.start()
        return True

    def on_stop(self):
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        if self._preloader is not None:
            self._preloader.wait(2000)
            self._preloader = None
        for w in list(self._local_workers):
            w.wait(3000)
        self._local_workers.clear()
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._mic_io = None
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
            self._play_io = None
        self._reset_vad()
        self.level_bar.setValue(0)
        self._style_go(False)
        self.target_combo.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.status_label.setText(tr("Connection closed", self._lang))

    def hideEvent(self, event):
        # Leaving the page must not keep the mic hot.
        if self._source is not None:
            self.on_stop()
        super().hideEvent(event)

    # --- audio I/O ---
    def _on_mic_ready(self):
        if self._mic_io is None:
            return
        data = bytes(self._mic_io.readAll().data())
        if not data:
            return
        fmt = self._in_fmt
        from PySide6.QtMultimedia import QAudioFormat
        if (fmt.sampleRate() == _IN_RATE and fmt.channelCount() == 1
                and fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16):
            pcm = data
        else:
            samples = _decode_to_mono_float(data, fmt.sampleFormat(), fmt.channelCount())
            samples = _resample(samples, fmt.sampleRate(), _IN_RATE)
            pcm = _encode_from_mono_float(samples, QAudioFormat.SampleFormat.Int16, 1)
        self._update_level(pcm)
        if self._mode == "google":
            if self._worker is not None:
                self._worker.send_audio(pcm)
        else:
            self._vad_feed(pcm)

    def _update_level(self, pcm):
        """Drive the mic level bar from the chunk's RMS (visual 'I hear you').
        Color: too quiet (gray-blue) -> good (green) -> too loud (red)."""
        import array
        import math
        from PySide6.QtGui import QColor
        a = array.array("h")
        a.frombytes(pcm)
        if not a:
            return
        rms = math.sqrt(sum((v / 32768.0) ** 2 for v in a) / len(a))
        pct = max(0, min(100, int(rms * 280)))
        color = QColor("#6b7a90") if pct < 10 else (
            QColor("#22c55e") if pct < 88 else QColor("#ef4444"))
        try:
            self.level_bar.setCustomBarColor(color, color)
        except Exception:  # noqa: BLE001 - older qfluentwidgets
            pass
        self.level_bar.setValue(pct)

    # --- local mode: energy VAD over 16k PCM16, dispatch each utterance ---
    def _reset_vad(self):
        self._vad_on = False
        self._vad_buf = bytearray()
        self._vad_preroll = bytearray()
        self._vad_voice_ms = 0.0
        self._vad_sil_ms = 0.0
        self._vad_floor = 0.003

    def _vad_feed(self, pcm):
        import array
        import math
        a = array.array("h")
        a.frombytes(pcm)
        if not a:
            return
        level = math.sqrt(sum((v / 32768.0) ** 2 for v in a) / len(a))
        dt_ms = len(a) / _IN_RATE * 1000.0
        on_th = max(_VAD_ON_ABS, self._vad_floor * _VAD_ON_MUL)
        off_th = max(_VAD_OFF_ABS, self._vad_floor * _VAD_OFF_MUL)
        # Always keep a rolling pre-roll of the most recent audio so the lead-in
        # before onset (often the first, key words) isn't lost.
        self._vad_preroll += pcm
        max_pre = int(_IN_RATE * _VAD_PREROLL_MS / 1000) * 2  # bytes (2/sample)
        if len(self._vad_preroll) > max_pre:
            del self._vad_preroll[:-max_pre]
        if not self._vad_on:
            if level < on_th:
                self._vad_floor = self._vad_floor * 0.99 + level * 0.01
            if level > on_th:
                self._vad_voice_ms += dt_ms
                if self._vad_voice_ms >= _VAD_ON_MS:
                    self._vad_on = True
                    self._vad_sil_ms = 0.0
                    # Start from the pre-roll (includes this chunk + lead-in).
                    self._vad_buf = bytearray(self._vad_preroll)
            else:
                self._vad_voice_ms = 0.0
        else:
            self._vad_buf += pcm
            self._vad_sil_ms = self._vad_sil_ms + dt_ms if level < off_th else 0.0
            dur_ms = len(self._vad_buf) / 2 / _IN_RATE * 1000.0
            if self._vad_sil_ms >= _VAD_HANG_MS or dur_ms >= _VAD_MAX_MS:
                utt = bytes(self._vad_buf)
                self._vad_on = False
                self._vad_voice_ms = 0.0
                self._vad_buf = bytearray()
                if dur_ms >= _VAD_MIN_MS:
                    self._dispatch_local(utt)

    def _dispatch_local(self, utt):
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        api_key = load_api_key_for_model(model) if online else ""
        dst = LANGUAGE_MAP.get(self.target_combo.currentText(), "en")
        w = LocalLiveWorker(utt, _IN_RATE, dst, model, online, api_key)
        w.recognized.connect(self._on_local_recognized)
        w.result.connect(self._on_local_result)
        w.failed.connect(lambda e: self.status_label.setText("error: " + e))
        w.finished.connect(lambda w=w: self._retire_local(w))
        self._local_workers.append(w)
        w.start()

    def _retire_local(self, w):
        if w in self._local_workers:
            self._local_workers.remove(w)

    def _on_preload_done(self, ready):
        if self._source is not None:
            self.status_label.setText(tr("Listening", self._lang))

    def _on_local_recognized(self, ts, source):
        # Source line shown as soon as it's recognized (before translation).
        if source:
            self.input_text.insertPlainText(f"[{ts}] {source}\n")
            self.input_text.ensureCursorVisible()
            if self._source is not None:
                self.status_label.setText(tr("Translating", self._lang))

    def _on_local_result(self, ts, translated):
        if translated:
            self.output_text.insertPlainText(f"[{ts}] {translated}\n")
            self.output_text.ensureCursorVisible()
        if self._source is not None:
            self.status_label.setText(tr("Listening", self._lang))

    def _play_audio(self, data):
        if self._play_io is None:
            return
        fmt = self._out_fmt
        from PySide6.QtMultimedia import QAudioFormat
        if (fmt.sampleRate() == _OUT_RATE and fmt.channelCount() == 1
                and fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16):
            out = data
        else:
            samples = _decode_to_mono_float(
                data, QAudioFormat.SampleFormat.Int16, 1)
            samples = _resample(samples, _OUT_RATE, fmt.sampleRate())
            out = _encode_from_mono_float(samples, fmt.sampleFormat(), fmt.channelCount())
        self._play_io.write(out)

    # --- worker signals ---
    def _append_input(self, text):
        self.input_text.insertPlainText(text)
        self.input_text.ensureCursorVisible()

    def _append_output(self, text):
        self.output_text.insertPlainText(text)
        self.output_text.ensureCursorVisible()

    def _on_status(self, status):
        if status == "listening":
            self.status_label.setText(tr("Listening", self._lang))
        elif status == "closed":
            self.status_label.setText(tr("Connection closed", self._lang))
        elif status.startswith("error:"):
            self.status_label.setText(status)
            self._info(status, error=True)

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Real-Time Voice", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)
