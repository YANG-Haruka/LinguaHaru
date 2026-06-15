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
import html as _html
import re

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QPushButton, QLabel,
    QTextEdit,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel, ComboBox,
    CardWidget, TextEdit, FluentIcon, InfoBar, PushButton, ToggleButton,
    InfoBarPosition, IconWidget,
)

from core import backend
from qt_app.i18n import tr
from qt_app.live_worker import (
    LiveWorker, LiveRecognizeWorker, LiveTranslateWorker, PreloadWorker)
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
# How often to re-recognize the growing utterance for streaming captions.
_VAD_PARTIAL_MS = 360


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


class _Waveform(QWidget):
    """iOS-style scrolling voice waveform: a fixed row of center-mirrored bars
    that bounce with the live mic level (green = good, gray = quiet, red = loud).
    Far more elegant than one long progress line."""

    def __init__(self, parent=None, bars=28):
        super().__init__(parent)
        self._n = bars
        self._levels = [0.0] * bars
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, level):
        self._levels.append(max(0.0, min(1.0, level)))
        self._levels = self._levels[-self._n:]
        self.update()

    def clear(self):
        self._levels = [0.0] * self._n
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        from PySide6.QtCore import Qt, QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        n = self._n
        gap = 4.0
        bw = max(2.0, (w - gap * (n - 1)) / n)
        cy = h / 2.0
        for i, lv in enumerate(self._levels):
            bh = max(3.0, lv * (h - 6))
            x = i * (bw + gap)
            if lv < 0.10:
                c = QColor("#6b7a90")
            elif lv < 0.88:
                c = QColor("#22c55e")
            else:
                c = QColor("#ef4444")
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, cy - bh / 2.0, bw, bh), bw / 2.0, bw / 2.0)
        p.end()


class _CaptionBar(QWidget):
    """Always-on-top, frameless, draggable, RESIZABLE floating caption window
    (like Windows Live Captions). Shows the latest source line and translated
    line over whatever else is on screen.

    Controls (compact top row): A- / A+ adjust the live font size; a mode button
    cycles Bilingual -> Translation Only -> Source Only; × closes. The window is
    resizable via a QSizeGrip in the bottom-right corner; dragging the body moves
    it. Translated text stays emphasized (larger/bold) relative to the source."""

    _FONT_MIN, _FONT_MAX = 12, 48
    _MODES = ("bilingual", "translation", "source")

    def __init__(self, parent=None, lang="en"):
        # No parent: a top-level Tool window that floats above other apps.
        super().__init__(None)
        self._lang = lang
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(320)
        self.setMaximumWidth(1600)
        self.setMinimumHeight(90)
        self.resize(560, 200)       # roomy default so several lines are visible
        self._drag_pos = None
        self._mode = "bilingual"
        # Base translated font size; the source line is kept smaller/emphasis-low.
        self._font_size = 20
        # Rolling buffer of recent utterances; a bigger window shows more lines.
        self._entries = []          # list of {"src": str, "dst": str}
        self._CAP_MAX = 60
        self._interim = ""          # live, not-yet-finalized source line

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._panel = QWidget(self)
        self._panel.setObjectName("captionPanel")
        self._panel.setStyleSheet(
            "#captionPanel{background-color: rgba(18,20,26,0.86);"
            "border-radius: 14px;}")
        root.addWidget(self._panel)

        inner = QVBoxLayout(self._panel)
        inner.setContentsMargins(18, 8, 18, 14)
        inner.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        self._mode_btn = self._mk_btn("", 70, 22)
        self._mode_btn.setToolTip(tr("Display Mode", self._lang))
        self._mode_btn.clicked.connect(self._cycle_mode)
        top.addWidget(self._mode_btn)
        top.addStretch(1)
        self._minus_btn = self._mk_btn("A-", 26, 22)
        self._minus_btn.setToolTip(tr("Font Size", self._lang))
        self._minus_btn.clicked.connect(lambda: self._bump_font(-2))
        top.addWidget(self._minus_btn)
        self._plus_btn = self._mk_btn("A+", 26, 22)
        self._plus_btn.setToolTip(tr("Font Size", self._lang))
        self._plus_btn.clicked.connect(lambda: self._bump_font(2))
        top.addWidget(self._plus_btn)
        self._close_btn = self._mk_btn("×", 22, 22, big=True)
        self._close_btn.clicked.connect(self.hide)
        top.addWidget(self._close_btn)
        inner.addLayout(top)

        # Auto-scrolling multi-line caption area: holds several recent lines and
        # pins the newest to the bottom, so resizing the window taller shows more.
        self._cap = QTextEdit(self._panel)
        self._cap.setReadOnly(True)
        self._cap.setFrameStyle(0)
        self._cap.setTextInteractionFlags(Qt.NoTextInteraction)  # let drag pass
        self._cap.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._cap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._cap.setStyleSheet(
            "QTextEdit{background:transparent;border:none;}")
        self._cap.setMinimumHeight(40)
        inner.addWidget(self._cap, 1)

        # Bottom-right grip lets the user resize the frameless window. It sits in
        # its own bottom row so it doesn't overlap the caption text.
        from PySide6.QtWidgets import QSizeGrip
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        self._grip = QSizeGrip(self._panel)
        self._grip.setFixedSize(14, 14)
        grip_row.addWidget(self._grip, 0, Qt.AlignRight | Qt.AlignBottom)
        inner.addLayout(grip_row)

        self._update_mode_btn()
        self._render()

    @staticmethod
    def _mk_btn(text, w, h, big=False):
        from PySide6.QtWidgets import QPushButton as _QPB
        b = _QPB(text)
        b.setFixedSize(w, h)
        b.setCursor(Qt.PointingHandCursor)
        fs = 18 if big else 12
        b.setStyleSheet(
            "QPushButton{background:transparent;color:#cbd5e1;border:none;"
            "font-size:%dpx;font-weight:600;}"
            "QPushButton:hover{color:#ffffff;}" % fs)
        return b

    def _bump_font(self, delta):
        new = max(self._FONT_MIN, min(self._FONT_MAX, self._font_size + delta))
        if new != self._font_size:
            self._font_size = new
            self._render()

    def _mode_label(self):
        if self._mode == "translation":
            return tr("Translation Only", self._lang)
        if self._mode == "source":
            return tr("Source Only", self._lang)
        return tr("Bilingual", self._lang)

    def _update_mode_btn(self):
        self._mode_btn.setText(self._mode_label())

    def _cycle_mode(self):
        i = self._MODES.index(self._mode)
        self._mode = self._MODES[(i + 1) % len(self._MODES)]
        self._update_mode_btn()
        self._render()

    def _render(self):
        """Rebuild the caption HTML from the rolling buffer and pin to bottom."""
        src_px = max(self._FONT_MIN - 4, int(self._font_size * 0.65))
        show_src = self._mode in ("bilingual", "source")
        show_dst = self._mode in ("bilingual", "translation")
        parts = []
        for e in self._entries:
            if show_src and e.get("src"):
                parts.append(
                    "<div style='color:#9aa6b2;font-size:%dpx;margin-top:8px;'>%s</div>"
                    % (src_px, _html.escape(e["src"])))
            if show_dst and e.get("dst"):
                parts.append(
                    "<div style='color:#f1f5f9;font-size:%dpx;font-weight:700;'>%s</div>"
                    % (self._font_size, _html.escape(e["dst"])))
        if self._interim and show_src:        # live, still-being-spoken text (dim)
            parts.append(
                "<div style='color:#9aa6b2;font-size:%dpx;margin-top:8px;'>%s …</div>"
                % (src_px, _html.escape(self._interim)))
        self._cap.setHtml("".join(parts))
        from PySide6.QtGui import QTextCursor
        self._cap.moveCursor(QTextCursor.End)      # keep newest visible
        self._cap.ensureCursorVisible()

    def set_source(self, text):
        text = (text or "").strip()
        if not text:
            return
        self._entries.append({"src": text, "dst": ""})
        self._trim()
        self._render()

    def set_translated(self, text):
        text = (text or "").strip()
        if not text:
            return
        if self._entries and not self._entries[-1].get("dst"):
            self._entries[-1]["dst"] = text     # pair with the source line
        else:
            self._entries.append({"src": "", "dst": text})
        self._trim()
        self._render()

    def set_interim(self, text):
        """Live, not-yet-finalized source text (shown dim under the committed
        lines). Cleared by passing an empty string."""
        new = (text or "").strip()
        if new != self._interim:
            self._interim = new
            self._render()

    def _trim(self):
        if len(self._entries) > self._CAP_MAX:
            self._entries = self._entries[-self._CAP_MAX:]

    def show_centered(self):
        """Place near the bottom-center of the primary screen, then show."""
        from PySide6.QtWidgets import QApplication
        self.show()
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + geo.height() - self.height() - 80
            self.move(max(geo.x(), x), max(geo.y(), y))
        self.raise_()

    # Drag the frameless window by pressing anywhere on it (the QSizeGrip in the
    # corner intercepts its own events, so resize still works).
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class _LoopbackWorker(QThread):
    """Capture system audio (speaker loopback) via the optional ``soundcard``
    package and emit 16 kHz mono int16 PCM chunks. Each chunk is delivered on
    the UI thread via the ``pcm`` signal (mirroring the mic ``readyRead`` flow).

    Fails safe: any record/import error is reported via ``failed`` and the
    thread exits cleanly without taking the page down."""

    pcm = Signal(bytes)
    failed = Signal(str)

    _BLOCK_SEC = 0.1  # ~100 ms blocks, like a mic readyRead cadence

    def __init__(self, loopback_id, parent=None):
        super().__init__(parent)
        self._loopback_id = loopback_id
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        try:
            import soundcard  # noqa: WPS433
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"soundcard unavailable: {e}")
            return
        try:
            mic = soundcard.get_microphone(
                self._loopback_id, include_loopback=True)
            rate = 48000  # device rate; resampled to 16 kHz below
            self._running = True
            with mic.recorder(samplerate=rate, channels=1) as rec:
                frames = int(rate * self._BLOCK_SEC)
                while self._running:
                    data = rec.record(numframes=frames)  # float32 [-1,1], (n, ch)
                    if data is None or len(data) == 0:
                        continue
                    samples = self._to_mono_floats(data)
                    samples = _resample(samples, rate, _IN_RATE)
                    if not samples:
                        continue
                    from PySide6.QtMultimedia import QAudioFormat
                    pcm = _encode_from_mono_float(
                        samples, QAudioFormat.SampleFormat.Int16, 1)
                    if pcm:
                        self.pcm.emit(pcm)
        except Exception as e:  # noqa: BLE001
            if self._running:
                self.failed.emit(f"loopback capture failed: {e}")
        finally:
            self._running = False

    @staticmethod
    def _to_mono_floats(data):
        """soundcard returns a numpy float32 array shaped (frames, channels).
        Average channels to mono and return a plain Python float list."""
        try:
            if hasattr(data, "ndim") and data.ndim > 1:
                data = data.mean(axis=1)
            return [float(v) for v in data]
        except Exception:  # noqa: BLE001
            return []


class LivePage(ScrollArea):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("LivePage")
        self._lang = lang
        self._mode = "local"    # "local" (SenseVoice+LLM) | "google" (Gemini Live)
        self.on_open_plugins = None  # set by MainWindow -> jump to Plugins page
        self._worker = None
        self._preloader = None
        self._local_workers = []
        self._source = None     # QAudioSource (mic)
        self._mic_io = None
        self._loopback = None    # _LoopbackWorker (system-audio capture)
        self._caption_bar = None  # _CaptionBar (floating subtitles)
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
        # streaming-recognition state (Windows-Live-Captions style)
        self._partial_ms = 0.0          # ms since the last partial dispatch
        self._stream_emitted = 0        # sentences committed in the current utterance
        self._recog_busy = False        # one STT worker in flight at a time
        self._recog_pending = None      # (pcm, is_final) — latest-wins while busy
        self._stream_detected = "auto"

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

        # --- Controls (two rows, like the Web layout) ---
        #   Row 1: voice mode + microphone
        #   Row 2: target language + volume meter + round start/stop button
        self._mode_ids = ["local", "google"]
        config = backend.read_config()
        ctrl_card = CardWidget()
        ctrl = QVBoxLayout(ctrl_card)
        ctrl.setContentsMargins(20, 14, 20, 14)
        ctrl.setSpacing(12)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.mode_label = BodyLabel(tr("Live Mode", lang))
        row1.addWidget(self.mode_label)
        self.mode_combo = ComboBox()
        self.mode_combo.addItems([tr("Local Voice Mode", lang), tr("Google Voice Mode", lang)])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row1.addWidget(self.mode_combo, 1)
        self.mic_label = BodyLabel(tr("Input", lang))
        row1.addWidget(self.mic_label)
        self.mic_combo = ComboBox()
        self._mic_devices = []
        # Per combo entry: None = real mic (QAudioDevice), else a loopback id
        # string for soundcard system-audio capture.
        self._mic_loopback_ids = []
        self._populate_mics()
        # Live input switching: when a session is already listening, changing the
        # source swaps just the capture device without stopping the session. The
        # signal is connected AFTER the initial populate; every repopulate blocks
        # signals (see _populate_mics), so this never fires on programmatic edits.
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        row1.addWidget(self.mic_combo, 1)
        ctrl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.target_label = BodyLabel(tr("Target Language", lang))
        row2.addWidget(self.target_label)
        self.target_combo = ComboBox()
        self.target_combo.addItems(backend.available_languages())
        self._set_combo(self.target_combo, config.get("default_dst_lang", "English"))
        row2.addWidget(self.target_combo, 1)
        self.mic_icon = IconWidget(FluentIcon.MICROPHONE, self)
        self.mic_icon.setFixedSize(18, 18)
        row2.addWidget(self.mic_icon)
        self.waveform = _Waveform(self)
        row2.addWidget(self.waveform, 1)
        # Single round start/stop button (green play -> red stop).
        self.go_btn = QPushButton("▶")
        self.go_btn.setFixedSize(46, 46)
        self.go_btn.clicked.connect(self._toggle_listen)
        self._style_go(False)
        row2.addWidget(self.go_btn)
        ctrl.addLayout(row2)

        # Floating-captions toggle: pops out an always-on-top subtitle window.
        cap_row = QHBoxLayout()
        cap_row.setSpacing(8)
        self.caption_btn = ToggleButton(FluentIcon.VIEW, tr("Floating Captions", lang))
        self.caption_btn.toggled.connect(self._on_caption_toggled)
        cap_row.addWidget(self.caption_btn)
        cap_row.addStretch(1)
        ctrl.addLayout(cap_row)
        layout.addWidget(ctrl_card)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(8)
        self.hint_label = CaptionLabel("")
        self.hint_label.setWordWrap(True)
        hint_row.addWidget(self.hint_label, 1)
        self.plugin_btn = PushButton(FluentIcon.APPLICATION, tr("Go to Plugins", lang))
        self.plugin_btn.clicked.connect(lambda: self.on_open_plugins and self.on_open_plugins())
        self.plugin_btn.hide()
        hint_row.addWidget(self.plugin_btn)
        layout.addLayout(hint_row)

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
        self.mic_label.setText(tr("Input", lang))
        self.caption_btn.setText(tr("Floating Captions", lang))
        self._populate_mics()
        self.input_header.setText(tr("Recognized Speech", lang))
        self.input_sub.setText(tr("Auto Detect", lang))
        self.output_header.setText(tr("Translation Result", lang))
        self.plugin_btn.setText(tr("Go to Plugins", lang))
        self._refresh_model_sub()
        self._update_hint()

    def _on_mode_changed(self, index):
        if 0 <= index < len(self._mode_ids):
            self._mode = self._mode_ids[index]
        self._update_hint()

    def _update_hint(self):
        """Show a hint when the chosen mode isn't ready (no plugin / no key)."""
        msg = ""
        need_plugin = False
        if self._mode == "local":
            if not video_translation_available():
                msg = tr("Local Voice Needs Plugin", self._lang)
                need_plugin = True
        else:
            if not load_api_key_for_model(_GOOGLE_PROVIDER):
                msg = tr("Google key not set", self._lang)
        self.hint_label.setText(msg)
        # Offer a one-click jump to the Plugins page when a plugin is missing.
        self.plugin_btn.setVisible(need_plugin)

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
        self._mic_loopback_ids = []
        # Mic entries first (default + each real input device).
        self.mic_combo.addItem(tr("Default Microphone", self._lang))
        self._mic_loopback_ids.append(None)
        for dev in self._mic_devices:
            self.mic_combo.addItem(dev.description())
            self._mic_loopback_ids.append(None)
        # System-audio (speaker loopback) entries, only if soundcard is present.
        for sid, name in self._loopback_sources():
            self.mic_combo.addItem(f"{tr('System Audio', self._lang)}: {name}")
            self._mic_loopback_ids.append(sid)
        idx = self.mic_combo.findText(prev)
        self.mic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mic_combo.blockSignals(False)

    @staticmethod
    def _loopback_sources():
        """List system-audio loopback sources as (id, name) tuples. Empty when
        the optional ``soundcard`` package isn't installed — mics-only then."""
        try:
            import soundcard  # noqa: WPS433
        except Exception:  # noqa: BLE001
            return []
        try:
            out = []
            for m in soundcard.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False):
                    out.append((m.id, m.name))
            return out
        except Exception:  # noqa: BLE001
            return []

    def _refresh_model_sub(self):
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        self.model_sub.setText(f"{tr('Current Model', self._lang)}: {model or '-'}")

    def _selected_loopback_id(self):
        """The soundcard loopback id if a System Audio entry is selected, else
        None (meaning: use the normal QAudioSource mic path)."""
        i = self.mic_combo.currentIndex()
        if 0 <= i < len(self._mic_loopback_ids):
            return self._mic_loopback_ids[i]
        return None

    def _selected_mic(self):
        """The chosen QAudioDevice, or None to mean the system default.

        Index 0 is the default mic; indices 1..len(mics) are real devices; any
        further indices are loopback entries (handled separately) and map to
        the default here (never reached for loopback selections)."""
        i = self.mic_combo.currentIndex()
        if 0 < i <= len(self._mic_devices):
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

    def _is_listening(self):
        """True while either the mic (QAudioSource) or the system-audio
        loopback worker is active."""
        return self._source is not None or self._loopback is not None

    def _toggle_listen(self):
        if self._is_listening():
            self.on_stop()
        else:
            self.on_start()

    def on_start(self):
        if self._is_listening():
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

    @staticmethod
    def _audio_fmt(rate):
        from PySide6.QtMultimedia import QAudioFormat
        f = QAudioFormat()
        f.setSampleRate(rate)
        f.setChannelCount(1)
        f.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return f

    def _open_input(self):
        """Open ONLY the currently-selected capture device (mic QAudioSource OR
        the soundcard loopback worker) and wire it to the pipeline. Returns True
        on success. Reused by both _start_audio and live input switching, so it
        must not touch playback/worker/VAD state. Assumes any prior input was
        already torn down by the caller."""
        try:
            from PySide6.QtMultimedia import QAudioSource, QMediaDevices
        except Exception as e:  # noqa: BLE001
            self._info(f"QtMultimedia unavailable: {e}", error=True)
            return False

        # System-audio (loopback) input: capture via the soundcard thread
        # instead of QAudioSource.
        loopback_id = self._selected_loopback_id()
        if loopback_id is not None:
            self._loopback = _LoopbackWorker(loopback_id, self)
            self._loopback.pcm.connect(self._on_loopback_pcm)
            self._loopback.failed.connect(self._on_loopback_failed)
            self._loopback.start()
            return True

        in_dev = self._selected_mic() or QMediaDevices.defaultAudioInput()
        if in_dev is None or in_dev.isNull():
            self._info(tr("No microphone found", self._lang), error=True)
            return False

        want_in = self._audio_fmt(_IN_RATE)
        self._in_fmt = want_in if in_dev.isFormatSupported(want_in) else in_dev.preferredFormat()
        self._source = QAudioSource(in_dev, self._in_fmt)
        self._mic_io = self._source.start()
        if self._mic_io is None:
            self._info(tr("No microphone found", self._lang), error=True)
            self._source = None
            return False
        self._mic_io.readyRead.connect(self._on_mic_ready)
        return True

    def _stop_input(self):
        """Tear down ONLY the capture device (mic or loopback), leaving the
        worker/VAD/playback running. Used by live input switching."""
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._mic_io = None
        if self._loopback is not None:
            self._loopback.stop()
            self._loopback.wait(2000)
            self._loopback = None

    def _start_audio(self, with_playback=True):
        """Open the mic (QAudioSource); also the speaker (QAudioSink) if needed."""
        if not self._open_input():
            return False

        if with_playback:
            try:
                from PySide6.QtMultimedia import QAudioSink, QMediaDevices
            except Exception as e:  # noqa: BLE001
                self._info(f"QtMultimedia unavailable: {e}", error=True)
                return False
            out_dev = QMediaDevices.defaultAudioOutput()
            want_out = self._audio_fmt(_OUT_RATE)
            self._out_fmt = (want_out if out_dev.isFormatSupported(want_out)
                             else out_dev.preferredFormat())
            self._sink = QAudioSink(out_dev, self._out_fmt)
            self._play_io = self._sink.start()
        return True

    def _on_mic_changed(self, index):
        """User picked a different input source. If a session is currently
        listening, swap just the capture device on the fly (keep worker/VAD/
        transcript). Otherwise it's just a stored selection (used at next start).
        Fail-safe: if the new device won't open, stop the whole session."""
        if not self._is_listening():
            return
        self._stop_input()
        # Reset the VAD so a half-captured utterance from the old device isn't
        # spliced onto the new one; the transcript/worker stay intact.
        self._reset_vad()
        self.waveform.clear()
        if not self._open_input():
            # _open_input already surfaced the error; stop cleanly.
            self.on_stop()
            return
        self.status_label.setText(tr("Listening", self._lang))

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
        self._save_live_history()       # persist this session's transcript
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._mic_io = None
        if self._loopback is not None:
            self._loopback.stop()
            self._loopback.wait(2000)
            self._loopback = None
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
            self._play_io = None
        self._reset_vad()
        self.waveform.clear()
        self._style_go(False)
        self.target_combo.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.status_label.setText(tr("Connection closed", self._lang))

    def _save_live_history(self):
        """Save the just-finished session (source + translation) to history."""
        src = [ln for ln in self.input_text.toPlainText().splitlines() if ln.strip()]
        dst = [ln for ln in self.output_text.toPlainText().splitlines() if ln.strip()]
        if not src and not dst:
            return
        try:
            from core.translation_history import save_live_session
            _, result_dir, log_dir = backend.get_custom_paths()
            online = backend.get_config("default_online", True)
            model = backend.get_active_model(online)
            save_live_session(src, dst, "Auto",
                              self.target_combo.currentText(), model, online,
                              result_dir, log_dir)
        except Exception:  # noqa: BLE001 — history is best-effort
            pass

    def hideEvent(self, event):
        # Leaving the page must not keep the mic/loopback hot or the bar open.
        if self._is_listening():
            self.on_stop()
        if self._caption_bar is not None:
            self._caption_bar.close()
            self._caption_bar = None
        if self.caption_btn.isChecked():
            self.caption_btn.blockSignals(True)
            self.caption_btn.setChecked(False)
            self.caption_btn.blockSignals(False)
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

    def _on_loopback_pcm(self, pcm):
        """System-audio loopback chunk (already 16 kHz mono PCM16). Feeds the
        SAME downstream pipeline the mic uses. Runs on the UI thread (queued
        signal), mirroring ``_on_mic_ready``."""
        if self._loopback is None or not pcm:
            return
        self._update_level(pcm)
        if self._mode == "google":
            if self._worker is not None:
                self._worker.send_audio(pcm)
        else:
            self._vad_feed(pcm)

    def _on_loopback_failed(self, msg):
        """Fail safe: report the error and stop cleanly (no crash)."""
        self.status_label.setText("error: " + msg)
        if self._is_listening():
            self.on_stop()

    def _update_level(self, pcm):
        """Feed the waveform from the chunk's RMS (visual 'I hear you')."""
        import array
        import math
        a = array.array("h")
        a.frombytes(pcm)
        if not a:
            return
        rms = math.sqrt(sum((v / 32768.0) ** 2 for v in a) / len(a))
        self.waveform.push(min(1.0, rms * 2.8))

    # --- local mode: energy VAD over 16k PCM16, dispatch each utterance ---
    def _reset_vad(self):
        self._vad_on = False
        self._vad_buf = bytearray()
        self._vad_preroll = bytearray()
        self._vad_voice_ms = 0.0
        self._vad_sil_ms = 0.0
        self._vad_floor = 0.003
        self._partial_ms = 0.0
        self._stream_emitted = 0
        self._recog_pending = None

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
                    self._partial_ms = 0.0
                    self._stream_emitted = 0       # new utterance
                    self._stream_detected = "auto"
                    # Start from the pre-roll (includes this chunk + lead-in).
                    self._vad_buf = bytearray(self._vad_preroll)
            else:
                self._vad_voice_ms = 0.0
        else:
            self._vad_buf += pcm
            self._vad_sil_ms = self._vad_sil_ms + dt_ms if level < off_th else 0.0
            dur_ms = len(self._vad_buf) / 2 / _IN_RATE * 1000.0
            self._partial_ms += dt_ms
            ended = self._vad_sil_ms >= _VAD_HANG_MS or dur_ms >= _VAD_MAX_MS
            if ended:
                utt = bytes(self._vad_buf)
                self._vad_on = False
                self._vad_voice_ms = 0.0
                self._vad_buf = bytearray()
                if dur_ms >= _VAD_MIN_MS:
                    self._dispatch_recognize(utt, is_final=True)   # flush remainder
            elif self._partial_ms >= _VAD_PARTIAL_MS and dur_ms >= _VAD_MIN_MS:
                self._partial_ms = 0.0
                self._dispatch_recognize(bytes(self._vad_buf), is_final=False)

    def _dispatch_recognize(self, pcm, is_final):
        """Run one STT pass (partial or final) on the utterance-so-far. Only one
        worker is in flight; while busy the newest request is queued (a final
        overrides a pending partial) so we never lag behind fast speech."""
        if self._recog_busy:
            if (is_final or self._recog_pending is None
                    or not self._recog_pending[1]):
                self._recog_pending = (pcm, is_final)
            return
        self._recog_busy = True
        w = LiveRecognizeWorker(pcm, _IN_RATE, is_final)
        w.done.connect(self._on_recognized_stream)
        w.finished.connect(lambda w=w: self._retire_local(w))
        self._local_workers.append(w)
        w.start()

    def _retire_local(self, w):
        if w in self._local_workers:
            self._local_workers.remove(w)

    def _on_preload_done(self, ready):
        if self._is_listening():
            self.status_label.setText(tr("Listening", self._lang))

    @staticmethod
    def _split_sents(text):
        """Finished sentences (kept with terminator) + trailing unfinished tail."""
        sents = [s.strip() for s in re.findall(r"[^。！？!?.]*[。！？!?.]", text or "")]
        sents = [s for s in sents if s]
        tail = re.sub(r"[^。！？!?.]*[。！？!?.]", "", text or "").strip()
        return sents, tail

    def _on_recognized_stream(self, text, detected, is_final):
        """Stable-prefix commit: a sentence is finalized (and translated) the
        moment the NEXT sentence starts appearing, so sentence 1 is translated
        while you're already speaking sentence 2."""
        self._recog_busy = False
        if detected:
            self._stream_detected = detected
        sents, tail = self._split_sents(text)
        if is_final:
            rest = sents[self._stream_emitted:]
            if tail:
                rest.append(tail)
            for s in rest:
                self._commit_stream_sentence(s)
            self._stream_emitted = 0
            self._set_caption_interim("")
            if self._is_listening():
                self.status_label.setText(tr("Listening", self._lang))
        else:
            confirmable = len(sents) if tail else max(0, len(sents) - 1)
            while self._stream_emitted < confirmable:
                self._commit_stream_sentence(sents[self._stream_emitted])
                self._stream_emitted += 1
            self._set_caption_interim("".join(sents[confirmable:]) + tail)
        if self._recog_pending is not None:        # process the freshest queued audio
            pcm, fin = self._recog_pending
            self._recog_pending = None
            self._dispatch_recognize(pcm, fin)

    def _commit_stream_sentence(self, source):
        source = (source or "").strip()
        if not source:
            return
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.input_text.insertPlainText(f"[{ts}] {source}\n")
        self.input_text.ensureCursorVisible()
        self._push_caption(source=source)
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        api_key = load_api_key_for_model(model) if online else ""
        dst = LANGUAGE_MAP.get(self.target_combo.currentText(), "en")
        w = LiveTranslateWorker(ts, source, self._stream_detected, dst, model,
                                online, api_key)
        w.done.connect(self._on_translated_stream)
        w.finished.connect(lambda w=w: self._retire_local(w))
        self._local_workers.append(w)
        w.start()

    def _on_translated_stream(self, ts, translated):
        if translated:
            self.output_text.insertPlainText(f"[{ts}] {translated}\n")
            self.output_text.ensureCursorVisible()
            self._push_caption(translated=translated)

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
        # Google fragments are incremental: mirror the panel's last line.
        self._push_caption(source=self._last_line(self.input_text))

    def _append_output(self, text):
        self.output_text.insertPlainText(text)
        self.output_text.ensureCursorVisible()
        self._push_caption(translated=self._last_line(self.output_text))

    @staticmethod
    def _last_line(text_edit):
        lines = [ln for ln in text_edit.toPlainText().splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def _on_status(self, status):
        if status == "listening":
            self.status_label.setText(tr("Listening", self._lang))
        elif status == "closed":
            self.status_label.setText(tr("Connection closed", self._lang))
        elif status.startswith("error:"):
            self.status_label.setText(status)
            self._info(status, error=True)

    # --- floating captions ---
    def _on_caption_toggled(self, checked):
        if checked:
            if self._caption_bar is None:
                self._caption_bar = _CaptionBar(lang=self._lang)
                self._caption_bar.destroyed.connect(self._on_caption_destroyed)
            self._caption_bar.show_centered()
        elif self._caption_bar is not None:
            self._caption_bar.hide()

    def _on_caption_destroyed(self, *args):
        self._caption_bar = None

    def _push_caption(self, source=None, translated=None):
        """Update the floating caption bar (if open) with the latest lines."""
        bar = self._caption_bar
        if bar is None:
            return
        if source is not None:
            bar.set_source(source)
        if translated is not None:
            bar.set_translated(translated)

    def _set_caption_interim(self, text):
        """Show the live, not-yet-finalized source text in the caption bar."""
        if self._caption_bar is not None:
            self._caption_bar.set_interim(text)

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Real-Time Voice", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)
