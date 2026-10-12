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
    InfoBarPosition, IconWidget, SwitchButton, CheckBox,
)

from core import backend
from qt_app.i18n import tr
from qt_app.live_worker import (
    LiveWorker, LiveRecognizeWorker, LiveTranslateWorker,
    LiveTranslateStreamWorker, PreloadWorker)
from core.api_keys import load_api_key_for_model
from core.languages_config import LANGUAGE_MAP, get_language_code
from core.optional_modules import realtime_voice_available

_GOOGLE_PROVIDER = "(Google) Live Translate"
_IN_RATE = 16000   # Gemini input / SenseVoice input
_OUT_RATE = 24000  # Gemini output

# Energy-VAD thresholds for the local mode (mirror the Web vad-worklet).
# Lowered so a soft voice still trips onset (the adaptive floor guards noise).
_VAD_ON_ABS, _VAD_ON_MUL = 0.006, 2.2
_VAD_OFF_ABS, _VAD_OFF_MUL = 0.004, 1.6
_VAD_ON_MS, _VAD_HANG_MS = 90, 900
_VAD_MIN_MS, _VAD_MAX_MS = 280, 15000
# Mic sensitivity preset -> (energy onset threshold, TEN-VAD threshold). Lower =
# more sensitive (picks up softer speech); higher = needs a louder voice (better
# in a noisy room). User-tunable in Settings (config live_vad_sensitivity).
_VAD_SENS = {"high": (0.004, 0.35), "standard": (0.006, 0.50), "low": (0.010, 0.65)}
# Lead-in kept before speech onset is confirmed, so the first words (often the
# key info) aren't clipped. Mirrors the Web vad-worklet's pre-roll ring buffer.
_VAD_PREROLL_MS = 500
# How often to re-recognize the growing utterance for streaming captions.
_VAD_PARTIAL_MS = 360
# Neural-VAD frame: TEN-VAD's hop (256 samples = 16 ms @ 16 kHz). The energy
# fallback uses the same frame size so the segmentation timing is identical.
_VAD_FRAME = 256
# Pseudo loopback ids: "whatever the default output device is right now" (so a
# multi-output PC doesn't capture a silent device), and macOS ScreenCaptureKit.
_DEFAULT_SPEAKER = "__default_speaker__"
_MAC_SYSTEM_AUDIO = "__mac_screencapturekit__"


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

    closed = Signal()             # hidden via its × (page unticks its toggle)
    _FONT_MIN, _FONT_MAX = 12, 96
    _MODES = ("bilingual", "translation", "source")
    # User-adjustable look (gear menu), persisted in config "caption_style".
    # bg is #AARRGGBB so its alpha doubles as the background opacity.
    _STYLE_DEFAULT = {"bg": "#db12141a", "dst_color": "#f1f5f9", "src_color": "#9aa6b2",
                      "family": "", "bold": True, "italic": False, "size": 20,
                      "mode": "bilingual", "geometry": None}

    def __init__(self, parent=None, lang="en"):
        # No parent + Qt.Window (NOT Qt.Tool): an INDEPENDENT top-level window.
        # A Qt.Tool window is owned by the app's main window and Windows hides it
        # automatically when the main window is minimized — which defeats the
        # whole point of a floating caption (you minimize the app to watch the
        # captions). Qt.Window keeps it visible on its own.
        super().__init__(None)
        self._lang = lang
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # No maximum size: it must stretch across an ultrawide / full screen.
        self.setMinimumWidth(240)
        self.setMinimumHeight(90)
        self.resize(560, 150)       # ~1-2 pairs visible; resize taller for more
        self._drag_pos = None
        saved = backend.get_config("caption_style") or {}
        self._style = dict(self._STYLE_DEFAULT,
                           **(saved if isinstance(saved, dict) else {}))
        self._mode = self._style["mode"] if self._style["mode"] in self._MODES else "bilingual"
        # Base translated font size; the source line is kept smaller/emphasis-low.
        try:
            self._font_size = max(self._FONT_MIN, min(self._FONT_MAX, int(self._style["size"])))
        except (TypeError, ValueError):
            self._font_size = 20
        # Rolling buffer of recent utterances. Only the newest few are RENDERED
        # (Google-Meet / 讯飞 style: current line + a little context, old lines
        # scroll away) — the full transcript lives in the main window's panels.
        self._entries = []          # list of {"src": str, "dst": str}
        self._CAP_MAX = 12          # kept for FIFO translation pairing
        self._RENDER_LAST = 3       # pairs shown at once
        self._interim = ""          # live, not-yet-finalized source line

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._panel = QWidget(self)
        self._panel.setObjectName("captionPanel")
        self._apply_bg()
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
        self._gear_btn = self._mk_btn("⚙", 26, 22)
        self._gear_btn.setToolTip(tr("Caption Style", self._lang))
        self._gear_btn.clicked.connect(self._show_style_menu)
        top.addWidget(self._gear_btn)
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
            self._style["size"] = new
            self._save_style()
            self._render()

    # --- appearance (gear menu) ---
    def _apply_bg(self):
        from PySide6.QtGui import QColor
        c = QColor(self._style["bg"])
        if not c.isValid():
            c = QColor(self._STYLE_DEFAULT["bg"])
        self._panel.setStyleSheet(
            "#captionPanel{background-color: rgba(%d,%d,%d,%.3f);border-radius: 14px;}"
            % (c.red(), c.green(), c.blue(), c.alphaF()))

    def _save_style(self):
        try:
            backend.set_config("caption_style", self._style)
        except Exception:  # noqa: BLE001 — a style save must never break captions
            pass

    def _show_style_menu(self):
        from PySide6.QtWidgets import QMenu
        L = self._lang
        m = QMenu(self)
        m.addAction(tr("Caption Background", L), lambda: self._pick_color("bg", True))
        m.addAction(tr("Translation Color", L), lambda: self._pick_color("dst_color"))
        m.addAction(tr("Source Color", L), lambda: self._pick_color("src_color"))
        m.addAction(tr("Caption Font", L), self._pick_font)
        m.addSeparator()
        m.addAction(tr("Reset Style", L), self._reset_style)
        m.exec(self._gear_btn.mapToGlobal(self._gear_btn.rect().bottomLeft()))

    def _pick_color(self, key, alpha=False):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        args = (QColor(self._style[key]), self, tr("Caption Style", self._lang))
        if alpha:   # alpha channel = background opacity
            c = QColorDialog.getColor(*args, QColorDialog.ColorDialogOption.ShowAlphaChannel)
        else:
            c = QColorDialog.getColor(*args)
        if c.isValid():
            self._style[key] = c.name(QColor.NameFormat.HexArgb if alpha
                                      else QColor.NameFormat.HexRgb)
            self._apply_bg()
            self._render()
            self._save_style()

    def _pick_font(self):
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QFontDialog
        f = QFont(self._style["family"] or self.font().family(), self._font_size)
        f.setBold(bool(self._style["bold"]))
        f.setItalic(bool(self._style["italic"]))
        ok, f = QFontDialog.getFont(f, self)
        if ok:
            self._style.update(family=f.family(), bold=f.bold(), italic=f.italic())
            if f.pointSize() > 0:
                self._font_size = max(self._FONT_MIN, min(self._FONT_MAX, f.pointSize()))
                self._style["size"] = self._font_size
            self._render()
            self._save_style()

    def _reset_style(self):
        self._style = dict(self._STYLE_DEFAULT, geometry=self._style.get("geometry"))
        self._font_size = self._STYLE_DEFAULT["size"]
        self._mode = self._STYLE_DEFAULT["mode"]
        self._update_mode_btn()
        self._apply_bg()
        self._render()
        self._save_style()

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
        self._style["mode"] = self._mode
        self._save_style()
        self._update_mode_btn()
        self._render()

    def _render(self):
        """Rebuild the caption HTML from the newest few entries and pin to
        bottom. Only _RENDER_LAST pairs are shown (current speech + a little
        context); the newest translation is bright, older ones dimmed — like
        Google Meet / 讯飞 captions, instead of a scrolling wall of history."""
        from PySide6.QtGui import QColor
        st = self._style
        src_px = max(self._FONT_MIN - 4, int(self._font_size * 0.65))
        show_src = self._mode in ("bilingual", "source")
        show_dst = self._mode in ("bilingual", "translation")
        src_c = _html.escape(st["src_color"])
        new_c, bg = QColor(st["dst_color"]), QColor(st["bg"])
        # Older translations: the chosen color blended 30% toward the background.
        old_c = QColor(int(new_c.red() * 0.7 + bg.red() * 0.3),
                       int(new_c.green() * 0.7 + bg.green() * 0.3),
                       int(new_c.blue() * 0.7 + bg.blue() * 0.3)).name()
        # Double quotes: the style attributes below are single-quoted.
        fam = ('font-family:"%s";' % _html.escape(st["family"])) if st["family"] else ""
        bold = 700 if st["bold"] else 400
        ital = "font-style:italic;" if st["italic"] else ""
        shown = self._entries[-self._RENDER_LAST:]
        parts = []
        for idx, e in enumerate(shown):
            newest = idx == len(shown) - 1
            if show_src and e.get("src"):
                parts.append(
                    "<div style='%scolor:%s;font-size:%dpx;margin-top:8px;'>%s</div>"
                    % (fam, src_c, src_px, _html.escape(e["src"])))
            if show_dst and e.get("dst"):
                color, weight = ((new_c.name(), bold) if newest
                                 else (old_c, max(400, bold - 100)))
                parts.append(
                    "<div style='%s%scolor:%s;font-size:%dpx;font-weight:%d;'>%s</div>"
                    % (fam, ital, color, self._font_size, weight, _html.escape(e["dst"])))
        if self._interim and show_src:        # live, still-being-spoken text (dim)
            parts.append(
                "<div style='%scolor:%s;font-size:%dpx;margin-top:8px;'>%s …</div>"
                % (fam, src_c, src_px, _html.escape(self._interim)))
        self._cap.setHtml("".join(parts))
        from PySide6.QtGui import QTextCursor
        self._cap.moveCursor(QTextCursor.End)      # keep newest visible
        self._cap.ensureCursorVisible()

    def set_source(self, text, key=None):
        text = (text or "").strip()
        if not text:
            return
        self._entries.append({"src": text, "dst": "", "key": key})
        self._trim()
        self._render()

    def set_translated(self, text, key=None):
        text = (text or "").strip()
        if not text:
            return
        if key is not None:
            # Pair by the sentence's own key. (FIFO pairing shifted EVERY later
            # caption by one line as soon as a single translation failed.)
            for e in self._entries:
                if e.get("key") == key:
                    e["dst"] = text
                    break
            else:
                return              # its source already scrolled out of the buffer
        else:
            # Keyless (Google mode): pair with the OLDEST source still waiting.
            for e in self._entries:
                if e.get("src") and not e.get("dst"):
                    e["dst"] = text
                    break
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
        """Restore the last position/size if it's still on a screen; otherwise
        place near the bottom-center of the primary screen. Then show."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication
        geo = self._style.get("geometry")
        if (isinstance(geo, list) and len(geo) == 4
                and QApplication.screenAt(QPoint(geo[0] + geo[2] // 2, geo[1] + 10))):
            self.setGeometry(*geo)
            self.show()
            self.raise_()
            return
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

    def hideEvent(self, event):
        # Remember where/how big the user left it (e.g. stretched full-width).
        g = self.geometry()
        self._style["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self._save_style()
        self.closed.emit()
        super().hideEvent(event)


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
        if self._loopback_id == _MAC_SYSTEM_AUDIO:
            self._run_mac()
            return
        try:
            import soundcard  # noqa: WPS433
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"soundcard unavailable: {e}")
            return
        try:
            lid = self._loopback_id
            if lid == _DEFAULT_SPEAKER:      # resolved at start: follows the OS
                lid = soundcard.default_speaker().id
            mic = soundcard.get_microphone(lid, include_loopback=True)
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

    def _run_mac(self):
        """macOS: capture system audio with ScreenCaptureKit (macOS 13+, needs
        the Screen Recording permission). soundcard can't — CoreAudio has no
        loopback device. SCK delivers 48 kHz mono float32 on its own queue."""
        import threading
        try:
            import numpy as np
            import ScreenCaptureKit as SCK
            import CoreMedia
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"macOS system audio needs macOS 13+ and the Real-Time "
                             f"Voice plugin reinstalled (pyobjc ScreenCaptureKit): {e}")
            return
        got, ev = {}, threading.Event()

        def _content(content, err):
            got["c"], got["e"] = content, err
            ev.set()
        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(_content)
        if not ev.wait(10) or got.get("c") is None or not got["c"].displays():
            self.failed.emit("macOS system audio: allow LinguaHaru in System Settings > "
                             f"Privacy & Security > Screen Recording, then restart it. ({got.get('e')})")
            return
        flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            got["c"].displays()[0], [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setExcludesCurrentProcessAudio_(True)
        cfg.setSampleRate_(48000)
        cfg.setChannelCount_(1)
        cfg.setWidth_(2)                    # video is required but unused: keep it tiny
        cfg.setHeight_(2)
        cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

        def _sink(sbuf):
            bb = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
            n = CoreMedia.CMBlockBufferGetDataLength(bb) if bb else 0
            if not n:
                return
            _st, data = CoreMedia.CMBlockBufferCopyDataBytes(bb, 0, n, None)
            a = np.frombuffer(bytes(data), dtype=np.float32)
            a = a[:len(a) // 3 * 3].reshape(-1, 3).mean(axis=1)   # 48k -> 16k
            self.pcm.emit((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())

        out = _mac_stream_output(SCK)
        _MAC_SINK[0] = _sink
        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(
            out, SCK.SCStreamOutputTypeAudio, None, None)
        if not ok:
            self.failed.emit(f"macOS system audio: {err}")
            return
        started = {}
        ev.clear()

        def _started(err):
            started["e"] = err
            ev.set()
        stream.startCaptureWithCompletionHandler_(_started)
        ev.wait(10)
        if started.get("e") is not None:
            self.failed.emit(f"macOS system audio: {started['e']}")
            return
        self._running = True
        while self._running:
            self.msleep(100)
        _MAC_SINK[0] = None
        stream.stopCaptureWithCompletionHandler_(lambda _err: None)

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


_MAC_OUTPUT_CLS = None
_MAC_SINK = [None]   # current audio callback (one capture at a time)


def _mac_stream_output(SCK):
    """One SCStreamOutput instance (the ObjC class can only be defined once per
    process). ``_MAC_SINK[0]`` receives each audio CMSampleBuffer."""
    global _MAC_OUTPUT_CLS
    if _MAC_OUTPUT_CLS is None:
        import objc
        from Foundation import NSObject

        class LHSystemAudioOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
                sink = _MAC_SINK[0]
                if kind == SCK.SCStreamOutputTypeAudio and sink:
                    try:
                        sink(sbuf)
                    except Exception:  # noqa: BLE001 — one bad buffer must not kill capture
                        pass
        _MAC_OUTPUT_CLS = LHSystemAudioOutput
    return _MAC_OUTPUT_CLS.alloc().init()


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
        self._overlay = None     # LoadingOverlay (model-loading glass lock)
        self._sink = None       # QAudioSink (playback)
        self._play_io = None
        self._in_fmt = None
        self._out_fmt = None
        # local-mode neural-VAD state (TEN-VAD, energy fallback)
        self._vad_on = False
        self._vad_buf = bytearray()
        self._vad_preroll = bytearray()
        self._vad_carry = bytearray()
        self._vad_voice_ms = 0.0
        self._vad_sil_ms = 0.0
        # streaming-recognition state (Windows-Live-Captions style)
        self._partial_ms = 0.0          # ms since the last partial dispatch
        self._stream_committed = ""     # raw text prefix committed this utterance
        self._stream_last = ""          # previous partial (LocalAgreement-2)
        self._ctx_history = []          # recent committed source lines (translation coherence)
        self._session_tokens = 0        # tokens used this session (for the thanks card)
        self._recog_busy = False        # one STT worker in flight at a time
        self._recog_pending = []        # queued (pcm, is_final); finals never dropped
        self._stream_detected = "auto"
        self._det_hist = []             # recent detections (majority-vote lock)
        self._stream_text = {}          # ts -> cumulative streamed translation
        self._out_rows = []             # [[sentence key, translation]] in source order
        self._rec = None                # wave writer while "Save Recording" is on
        self._rec_path = ""
        self._rec_start = None

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
        self.mode_combo.addItems(self._mode_items())
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
        # Keep this session's audio (WAV) next to its transcript; History plays
        # it and summarizes the session on demand.
        self.save_rec_cb = CheckBox(tr("Save Recording", lang))
        self.save_rec_cb.setToolTip(tr("Save Recording Tip", lang))
        self.save_rec_cb.setChecked(bool(config.get("live_save_recording", False)))
        self.save_rec_cb.toggled.connect(
            lambda on: backend.set_config("live_save_recording", bool(on)))
        cap_row.addWidget(self.save_rec_cb)
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
        self.mode_combo.addItems(self._mode_items())
        self.mode_combo.setCurrentIndex(max(0, cur))
        self.mode_combo.blockSignals(False)
        self.target_label.setText(tr("Target Language", lang))
        self.mic_label.setText(tr("Input", lang))
        self.caption_btn.setText(tr("Floating Captions", lang))
        self.save_rec_cb.setText(tr("Save Recording", lang))
        self.save_rec_cb.setToolTip(tr("Save Recording Tip", lang))
        self._populate_mics()
        self.input_header.setText(tr("Recognized Speech", lang))
        self.input_sub.setText(tr("Auto Detect", lang))
        self.output_header.setText(tr("Translation Result", lang))
        self.plugin_btn.setText(tr("Go to Plugins", lang))
        self._refresh_model_sub()
        self._update_hint()

    def _mode_items(self):
        """Mode combo labels; the local one names the STT model actually in use
        (it said "SenseVoice" even while Qwen3-ASR was running)."""
        try:
            from core.pipelines.video_translation_pipeline import (
                get_selected_live_stt_model, get_stt_model)
            stt = get_stt_model(get_selected_live_stt_model())["label"].split(" — ")[0]
        except Exception:  # noqa: BLE001 — plugin missing: keep a generic name
            stt = "ASR"
        return [tr("Local Voice Mode", self._lang).replace("{stt}", stt),
                tr("Google Voice Mode", self._lang)]

    def _on_mode_changed(self, index):
        if 0 <= index < len(self._mode_ids):
            self._mode = self._mode_ids[index]
        self._update_hint()

    def _update_hint(self):
        """Show a hint when the chosen mode isn't ready (no plugin / no key)."""
        msg = ""
        need_plugin = False
        if self._mode == "local":
            if not realtime_voice_available():
                msg = tr("Local Voice Needs Plugin", self._lang)
                need_plugin = True
        else:
            if not load_api_key_for_model(_GOOGLE_PROVIDER):
                msg = tr("Google key not set", self._lang)
        self.hint_label.setText(msg)
        # Offer a one-click jump to the Plugins page when a plugin is missing.
        self.plugin_btn.setVisible(need_plugin)

    def reload_languages(self):
        """Re-read the language list (a custom language was just added) without
        losing the current selection. Called by the settings page."""
        try:
            keep = self.target_combo.currentText()
            self.target_combo.blockSignals(True)
            self.target_combo.clear()
            self.target_combo.addItems(backend.available_languages())
            self.target_combo.blockSignals(False)
            self._set_combo(self.target_combo, keep)
        except Exception:  # noqa: BLE001 — a refresh must never break the page
            pass

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
            if sid in (_DEFAULT_SPEAKER, _MAC_SYSTEM_AUDIO):
                name = tr("Default Output Device", self._lang)
            self.mic_combo.addItem(f"{tr('System Audio', self._lang)}: {name}")
            self._mic_loopback_ids.append(sid)
        prev = prev or backend.get_config("live_input", "")
        idx = self.mic_combo.findText(prev)
        self.mic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.mic_combo.blockSignals(False)

    @staticmethod
    def _loopback_sources():
        """List system-audio loopback sources as (id, name) tuples. Empty when
        the optional ``soundcard`` package isn't installed — mics-only then."""
        import sys
        from core.log_config import app_logger
        if sys.platform == "darwin":
            # CoreAudio has no loopback; ScreenCaptureKit (macOS 13+) does.
            try:
                import ScreenCaptureKit  # noqa: F401, WPS433
                return [(_MAC_SYSTEM_AUDIO, "")]
            except Exception as e:  # noqa: BLE001
                app_logger.warning(f"System audio unavailable on macOS: {e}")
                return []
        try:
            import soundcard  # noqa: WPS433
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"System audio unavailable (soundcard): {e}")
            return []
        try:
            out = []
            for m in soundcard.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False):
                    out.append((m.id, m.name))
            # First: follow the current default output (the one actually playing).
            return [(_DEFAULT_SPEAKER, "")] + out if out else []
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Listing system audio devices failed: {e}")
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
        if not self._is_listening():
            cur = self.mode_combo.currentIndex()
            self.mode_combo.blockSignals(True)
            self.mode_combo.clear()
            self.mode_combo.addItems(self._mode_items())
            self.mode_combo.setCurrentIndex(max(0, cur))
            self.mode_combo.blockSignals(False)
        # Warm the local STT model in the background as soon as the page is
        # opened, so pressing Start doesn't sit through the library import +
        # model load (~10 s on a cold start).
        if self._mode == "local" and realtime_voice_available():
            from core.pipelines.video_translation_pipeline import recognizer_ready
            if not recognizer_ready():
                self._start_preload()
        super().showEvent(event)

    def _start_preload(self):
        if self._preloader is not None and self._preloader.isRunning():
            return
        self._preloader = PreloadWorker(self)
        self._preloader.done.connect(self._on_preload_done)
        self._preloader.start()

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
        if self._mode == "local" and not realtime_voice_available():
            self._info(tr("Local Voice Needs Plugin", self._lang), error=True)
            return

        self.input_text.clear()
        self.output_text.clear()
        self._session_tokens = 0
        self._stream_text = {}          # drop any leftover streamed-translation state
        self._out_rows = []
        self._reset_vad()

        # Google mode needs playback (it returns spoken audio); local mode is
        # text-only, so skip the speaker.
        # Google mode speaks its translation aloud — but not while capturing the
        # system audio, or it would hear (and re-translate) itself.
        if not self._start_audio(with_playback=(self._mode == "google"
                                                and self._selected_loopback_id() is None)):
            return

        self._start_recording()

        if self._mode == "google":
            target = get_language_code(self.target_combo.currentText())
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
                self._show_loading()
                self._start_preload()   # joins a background preload already running

        # Real-time voice is in progress -> keep the system awake (and, with the
        # process-wide opt-out, un-throttled) until on_stop.
        try:
            from core.power import begin_activity
            begin_activity()
            self._power_held = True
        except Exception:  # noqa: BLE001
            pass

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
        # macOS: Qt claims 16 kHz but its CoreAudio conversion delivers ~22k
        # frames/s labeled as 16k (slowed, low-pitched audio -> bad STT).
        # Capture the device's native format and resample ourselves instead.
        import sys
        if sys.platform == "darwin" or not in_dev.isFormatSupported(want_in):
            self._in_fmt = in_dev.preferredFormat()
        else:
            self._in_fmt = want_in
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
        backend.set_config("live_input", self.mic_combo.currentText())
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
        # on_stop also runs on app close / page leave; only a session that was
        # actually running gets saved (closing the app used to save the last,
        # already-saved session a second time).
        was_live = self._is_listening() or self._worker is not None
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        if self._preloader is not None:
            self._preloader.wait(2000)
            self._preloader = None
        # Best-effort wait for in-flight STT/translate workers, but NEVER drop a
        # reference to a thread that's still running (Qt would destroy it mid-run
        # and abort). Finished ones are removed; still-running ones retire
        # themselves via their finished -> _retire_local signal.
        for w in list(self._local_workers):
            if w.isRunning():
                w.wait(3000)
        self._local_workers = [w for w in self._local_workers if w.isRunning()]
        audio = self._stop_recording()
        if was_live:
            self._save_live_history(audio)  # persist this session's transcript (+ audio)
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
        if getattr(self, "_power_held", False):   # balance begin_activity()
            self._power_held = False
            try:
                from core.power import end_activity
                end_activity()
            except Exception:  # noqa: BLE001
                pass

    # --- recording (WAV of exactly what the recognizer heard: 16 kHz mono) ---
    def _start_recording(self):
        self._rec, self._rec_path = None, ""
        from datetime import datetime
        self._rec_start = datetime.now()
        if not self.save_rec_cb.isChecked():
            return
        import os
        import wave
        try:
            _, result_dir, _ = backend.get_custom_paths()
            os.makedirs(result_dir, exist_ok=True)
            self._rec_path = os.path.join(
                result_dir, f"realtime_{self._rec_start.strftime('%Y%m%d_%H%M%S')}.wav")
            self._rec = wave.open(self._rec_path, "wb")
            self._rec.setnchannels(1)
            self._rec.setsampwidth(2)
            self._rec.setframerate(_IN_RATE)
        except Exception as e:  # noqa: BLE001 — never block live voice on this
            self._rec, self._rec_path = None, ""
            self._info(f"{tr('Save Recording', self._lang)}: {e}", error=True)

    def _record(self, pcm):
        if self._rec is not None:
            try:
                self._rec.writeframes(pcm)
            except Exception:  # noqa: BLE001 — e.g. disk full: stop recording, keep going
                self._stop_recording()

    def _stop_recording(self):
        """Close the WAV; returns its path ('' when nothing was recorded)."""
        rec, self._rec = self._rec, None
        if rec is None:
            return ""
        try:
            rec.close()
        except Exception:  # noqa: BLE001
            pass
        return self._rec_path

    def _save_live_history(self, audio_path=""):
        """Save the just-finished session (source + translation) to history."""
        src = [ln for ln in self.input_text.toPlainText().splitlines() if ln.strip()]
        dst = [ln for ln in self.output_text.toPlainText().splitlines() if ln.strip()]
        if not src and not dst and not audio_path:
            return
        try:
            from core.translation_history import save_live_session
            _, result_dir, _ = backend.get_custom_paths()
            online = backend.get_config("default_online", True)
            model = backend.get_active_model(online)
            tokens = int(self._session_tokens or 0)
            # history_dir(), the DB the History page reads — the legacy log_dir
            # made every desktop real-time session invisible in History.
            save_live_session(src, dst, "Auto",
                                    self.target_combo.currentText(), model, online,
                                    result_dir, backend.history_dir(), total_tokens=tokens,
                                    cost_amount=None, cost_currency=None,
                                    start_time=self._rec_start, audio_path=audio_path)
            if not getattr(self, "_shutting_down", False):   # no modal during app close
                from qt_app.thanks import show_thanks
                show_thanks(self.window(), self._lang, tokens)
        except Exception:  # noqa: BLE001 — history is best-effort
            pass

    def hideEvent(self, event):
        # Leaving the page must not keep the mic/loopback hot or the bar open.
        # A SPONTANEOUS hide is the window being minimized — that's exactly when
        # the floating captions are needed, so keep listening and keep the bar.
        if event.spontaneous():
            super().hideEvent(event)
            return
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
        self._record(pcm)
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
        self._record(pcm)
        self._update_level(pcm)
        if self._mode == "google":
            if self._worker is not None:
                self._worker.send_audio(pcm)
        else:
            self._vad_feed(pcm)

    def _on_loopback_failed(self, msg):
        """Fail safe: stop cleanly, THEN say why. (Setting the status first let
        on_stop overwrite it with "Connection closed" — the reason never showed
        and nothing was logged.)"""
        from core.log_config import app_logger
        app_logger.warning(f"System audio capture failed: {msg}")
        if self._is_listening():
            self.on_stop()
        if "-3801" in msg or "TCC" in msg or "Screen Recording" in msg:
            msg = tr("System Audio Permission", self._lang)   # macOS privacy denial
        self.status_label.setText("⚠ " + msg)
        self._info(msg, error=True, duration=-1)              # stays until closed

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

    # --- local mode: neural VAD (TEN-VAD) over 16k PCM16, per 16ms frame ---
    def _reset_vad(self):
        # Trailing-silence (hang) that ends an utterance — user-tunable in
        # Settings so slow speakers can keep their phrases from being chopped.
        try:
            self._hang_ms = float(backend.get_config("live_vad_hang_ms", _VAD_HANG_MS))
        except (TypeError, ValueError):
            self._hang_ms = float(_VAD_HANG_MS)
        # Mic sensitivity (onset/neural threshold) + force-cut ceiling, both
        # user-tunable in Settings.
        self._on_abs, ten_th = _VAD_SENS.get(
            backend.get_config("live_vad_sensitivity", "standard"), _VAD_SENS["standard"])
        try:
            self._max_seg_ms = float(backend.get_config("live_vad_max_seg_ms", _VAD_MAX_MS))
        except (TypeError, ValueError):
            self._max_seg_ms = float(_VAD_MAX_MS)
        # Rebuild the cached TEN-VAD if the sensitivity threshold changed.
        if getattr(self, "_ten_vad_threshold", None) != ten_th:
            self._ten_vad_threshold = ten_th
            if hasattr(self, "_ten_vad"):
                del self._ten_vad
        self._vad_on = False
        self._vad_buf = bytearray()
        self._vad_preroll = bytearray()
        self._vad_carry = bytearray()     # leftover < one frame
        self._vad_voice_ms = 0.0
        self._vad_sil_ms = 0.0
        self._partial_ms = 0.0
        self._stream_committed = ""
        self._stream_last = ""
        self._stream_detected = "auto"
        self._det_hist = []
        self._ctx_history = []
        self._held = ""                   # backchannels waiting to ride with the next line
        self._recog_pending = []
        # Clear any in-flight STT marker too: if a worker from a previous session
        # outlived on_stop()'s wait, this stops the new session from queueing all
        # audio behind a stale busy flag (a late result is ignored via the
        # _is_listening() guard in _on_recognized_stream).
        self._recog_busy = False

    def _vad_model(self):
        """Lazy TEN-VAD (neural, noise-robust, ~0.1ms/frame). None -> energy
        fallback. Picked over Silero/WebRTC: fastest endpointing + tiny + holds
        up in noise where the old energy VAD flagged everything as speech."""
        if not hasattr(self, "_ten_vad"):
            try:
                from ten_vad import TenVad
                self._ten_vad = TenVad(hop_size=_VAD_FRAME,
                                       threshold=getattr(self, "_ten_vad_threshold", 0.5))
            except Exception:  # noqa: BLE001 — lib missing -> energy fallback
                self._ten_vad = None
        return self._ten_vad

    def _frame_is_speech(self, frame_np):
        vad = self._vad_model()
        if vad is not None:
            try:
                _prob, flag = vad.process(frame_np)
                return bool(flag)
            except Exception:  # noqa: BLE001 — disable on error, fall back
                self._ten_vad = None
        import numpy as np
        lvl = float(np.sqrt(np.mean((frame_np.astype("float32") / 32768.0) ** 2)))
        return lvl > getattr(self, "_on_abs", _VAD_ON_ABS)

    def _vad_feed(self, pcm):
        import numpy as np
        self._vad_carry += pcm
        fb = _VAD_FRAME * 2     # bytes per frame (int16)
        while len(self._vad_carry) >= fb:
            frame = bytes(self._vad_carry[:fb])
            del self._vad_carry[:fb]
            self._vad_step(frame, np.frombuffer(frame, dtype=np.int16))

    def _vad_step(self, frame, frame_np):
        """Run the onset/hang/segment state machine for one 16 ms frame."""
        dt_ms = _VAD_FRAME / _IN_RATE * 1000.0       # 16 ms
        speech = self._frame_is_speech(frame_np)
        # Rolling pre-roll so the lead-in before onset (key first words) isn't lost.
        self._vad_preroll += frame
        max_pre = int(_IN_RATE * _VAD_PREROLL_MS / 1000) * 2
        if len(self._vad_preroll) > max_pre:
            del self._vad_preroll[:-max_pre]
        if not self._vad_on:
            if speech:
                self._vad_voice_ms += dt_ms
                if self._vad_voice_ms >= _VAD_ON_MS:
                    self._vad_on = True
                    self._vad_sil_ms = 0.0
                    self._partial_ms = 0.0
                    # The per-utterance stream state (committed text, last
                    # partial, language vote) is NOT reset here: the previous
                    # utterance's final STT result may still be in flight, and
                    # resetting now made it re-commit that WHOLE utterance —
                    # every sentence shown and translated twice, worst when
                    # speech runs into the 30 s cap and the next utterance starts
                    # at once. It is reset where that final is processed.
                    self._vad_buf = bytearray(self._vad_preroll)
            else:
                self._vad_voice_ms = 0.0
        else:
            self._vad_buf += frame
            self._vad_sil_ms = 0.0 if speech else self._vad_sil_ms + dt_ms
            dur_ms = len(self._vad_buf) / 2 / _IN_RATE * 1000.0
            self._partial_ms += dt_ms
            # Progressive silence: the longer the utterance, the shorter the pause
            # needed to end it — lower latency on long speech (LiveTranslate-style).
            # The floor stays >=500ms so a slow speaker's natural between-phrase
            # pauses don't chop a sentence into fragments. Mirrors the Web worklet.
            base_hang = getattr(self, "_hang_ms", float(_VAD_HANG_MS))
            if dur_ms < 4000:
                hang = base_hang
            elif dur_ms < 8000:
                hang = base_hang * 0.7
            else:
                hang = max(500.0, base_hang * 0.55)
            capped = (self._vad_sil_ms < hang
                      and dur_ms >= getattr(self, "_max_seg_ms", _VAD_MAX_MS))
            if capped:
                # Hit the length cap mid-speech (fast talker, no pause). Cutting
                # at this exact frame split words in half ("大家好" -> "大"+"今…").
                # Cut at the quietest moment of the last 2 s instead, and carry
                # the audio after the cut into the next utterance — nothing is
                # dropped or recognized twice. No real dip there (continuous
                # Japanese: "プロジェク"/"トを…")? Keep listening for one, up to
                # 1.5x the cap, then cut at the quietest point anyway.
                cut, depth = self._quiet_cut(self._vad_buf)
                if depth > 0.15 and dur_ms < 1.5 * getattr(self, "_max_seg_ms", _VAD_MAX_MS):
                    if self._partial_ms >= _VAD_PARTIAL_MS:
                        self._partial_ms = 0.0
                        self._dispatch_recognize(bytes(self._vad_buf), is_final=False)
                    return
                utt = bytes(self._vad_buf[:cut])
                self._vad_buf = bytearray(self._vad_buf[cut:])
                self._partial_ms = 0.0
                self._dispatch_recognize(utt, is_final=True)
                return
            if self._vad_sil_ms >= hang:
                utt = bytes(self._vad_buf)
                self._vad_on = False
                self._vad_voice_ms = 0.0
                self._vad_buf = bytearray()
                # The pre-roll still holds this utterance's last 500 ms. After a
                # silence end that is only silence, but after the 30 s cap the
                # next utterance starts at once and would re-recognize it — the
                # same words twice at the seam ("图像。" then "像啊…").
                self._vad_preroll = bytearray()
                if dur_ms >= _VAD_MIN_MS:
                    self._dispatch_recognize(utt, is_final=True)   # flush remainder
            elif self._partial_ms >= _VAD_PARTIAL_MS and dur_ms >= _VAD_MIN_MS:
                self._partial_ms = 0.0
                self._dispatch_recognize(bytes(self._vad_buf), is_final=False)

    @staticmethod
    def _quiet_cut(buf):
        """(byte offset, depth) of the quietest ~80 ms window in the last 2 s
        (and the back half) of PCM16 ``buf`` — where a forced cut is least likely
        to split a word. depth = that window's energy / the median window's:
        near 0 is a real pause, near 1 means there's no pause to cut at."""
        import numpy as np
        a = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32)
        win = _VAD_FRAME * 5
        lo = max(len(a) - _IN_RATE * 2, len(a) // 2)
        n = (len(a) - lo) // win
        if n < 2:
            return len(buf), 0.0
        energy = (a[lo:lo + n * win].reshape(n, win) ** 2).mean(axis=1)
        k = int(energy.argmin())
        depth = float(energy[k] / max(float(np.median(energy)), 1e-9))
        return (lo + k * win + win // 2) * 2, depth

    def _dispatch_recognize(self, pcm, is_final):
        """Run one STT pass (partial or final) on the utterance-so-far. Only one
        worker is in flight; while busy requests queue in order, stale partials
        dropped, so we never lag behind fast speech."""
        if not self._is_listening():
            return   # session stopped — don't start new STT work
        if self._recog_busy:
            # Results must come back in dispatch order and a FINAL must never be
            # dropped: it covers its whole utterance, and processing it is what
            # closes that utterance's stream state. (The old single slot let a
            # second final overwrite the first.) Queued partials go stale as soon
            # as anything newer arrives, so drop those; finals stay in line.
            self._recog_pending = [q for q in self._recog_pending if q[1]]
            self._recog_pending.append((pcm, is_final))
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
        self._hide_loading()
        if self._is_listening():
            self.status_label.setText(tr("Listening", self._lang))

    def _show_loading(self, text=None):
        """Glass-lock the whole window while the STT model loads."""
        if self._overlay is None:
            from qt_app.loading_overlay import LoadingOverlay
            self._overlay = LoadingOverlay(self.window(), self._lang)
        self._overlay.show_with(text)

    def _hide_loading(self):
        if self._overlay is not None:
            self._overlay.hide()

    # Scored sentence boundaries + LocalAgreement-2 prefix commit. Twin of the
    # Web app.js logic: streaming STT revises its tail and re-segments, so we
    # commit by stable CHARACTER PREFIX (agreed by 2 consecutive partials) and
    # break at the most natural point — sentence punct > clause comma > space >
    # CJK connective > (last resort) a hard cap — measuring length in CELLS
    # (CJK = 2, latin = 1) so a caption line looks balanced regardless of script.
    _SENT_END = "。！？!?."
    _CLAUSE = "、，,；;"
    _CONNECTIVES = ("然后", "然後", "但是", "所以", "因为", "因為", "如果", "不过",
                    "不過", "而且", "还有", "還有", "其实", "其實", "因此", "于是",
                    "於是", "可是", "虽然", "雖然", "这样", "這樣")
    _MIN_CELLS, _TARGET_CELLS, _HARD_CELLS = 24, 60, 88
    # SenseVoice ends EVERY partial with "。", so a bare full stop is weak
    # evidence: it ends a unit only after >= 8 cells, and in a partial only when
    # more agreed text follows it (else "…多分。"/"あのアプリの。" fragments).
    _SENT_MIN_CELLS = 8

    @staticmethod
    def _cell(ch):
        return 2 if re.search(r"[　-鿿＀-￯]", ch) else 1

    @classmethod
    def _conn_at(cls, text, i):
        return any(text.startswith(w, i) for w in cls._CONNECTIVES)

    @classmethod
    def _find_boundary(cls, text, start, final):
        """Index to end the unit starting at `start`, or -1 to wait for more."""
        w = 0
        comma = space = conn = -1
        for i in range(start, len(text)):
            ch = text[i]
            w += cls._cell(ch)
            if ch in cls._SENT_END and w >= cls._SENT_MIN_CELLS and (
                    final or i + 1 < len(text)):
                return i + 1                       # best: a full stop
            if w >= cls._MIN_CELLS:
                if ch in cls._CLAUSE:
                    comma = i + 1
                elif ch == " ":
                    space = i + 1
                elif i > start and cls._conn_at(text, i):
                    conn = i                       # cut BEFORE the connective
            if w >= cls._HARD_CELLS:               # last resort: must break
                if comma > 0:
                    return comma
                if space > 0:
                    return space
                if conn > 0:
                    return conn
                return cls._soft_cut(text, start, i + 1)   # no punctuation at all
        if not final and w >= cls._TARGET_CELLS:
            if comma > 0:
                return comma
            if space > 0:
                return space
            if conn > 0:
                return conn
        return -1

    # Small kana / long-vowel mark: never start a unit with one.
    _SMALL_KANA = "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー"

    @staticmethod
    def _script(ch):
        o = ord(ch)
        if 0x3040 <= o <= 0x309F:
            return "H"                     # hiragana
        if 0x30A0 <= o <= 0x30FF:
            return "K"                     # katakana
        if 0x3400 <= o <= 0x9FFF:
            return "C"                     # kanji / hanzi
        return "O"

    @classmethod
    def _soft_cut(cls, text, start, end):
        """Hard-length break for unpunctuated run-on speech (SenseVoice leaves
        Japanese monologue without punctuation): instead of cutting at exactly
        `end` — which split katakana loanwords and verb chains ("…せて|いただいて")
        — cut at the last hiragana -> kanji/katakana transition in the final 16
        chars (a particle boundary: "…の|会議", "…を|開始"). Failing that, never
        cut inside a katakana run."""
        for j in range(end - 1, max(start + 1, end - 16) - 1, -1):
            if (cls._script(text[j - 1]) == "H" and cls._script(text[j]) in "CK"
                    and text[j] not in cls._SMALL_KANA):
                return j
        k = end
        while (k > start + 1 and k < len(text) and cls._script(text[k - 1]) == "K"
               and cls._script(text[k]) == "K"):
            k -= 1
        return k

    @classmethod
    def _split_scored(cls, text, final):
        """(units, consumed): ready units + the RAW length forming complete units
        (so the caller advances its committed prefix exactly). When final, the
        trailing remainder is flushed as a last unit."""
        units = []
        i = 0
        while i < len(text):
            cut = cls._find_boundary(text, i, final)
            if cut < 0:
                break
            seg = text[i:cut].strip()
            if seg:
                units.append(seg)
            i = cut
        consumed = i
        if final:
            seg = text[i:].strip()
            if seg:
                units.append(seg)
            consumed = len(text)
        return units, consumed

    @staticmethod
    def _common_prefix(a, b):
        n = min(len(a), len(b))
        i = 0
        while i < n and a[i] == b[i]:
            i += 1
        return a[:i]

    def _uncommitted_tail(self, text):
        """The part of a hypothesis that is NOT covered by what we already
        committed. Committed text must NEVER be re-emitted, even when the STT
        (which re-decodes the whole window each pass) REWRITES the beginning of
        the utterance — e.g. \"就比如说…\" becoming \"大哥就比如说…\". The old
        prefix-rollback approach re-committed (= duplicated on screen) the whole
        utterance in that case. Alignment order:
          1) exact: hypothesis starts with the committed text;
          2) content: locate the last ~10 committed chars inside the hypothesis
             (survives head rewrites);
          3) length: skip len(committed) chars (same audio ≈ same length)."""
        c = self._stream_committed
        if not c:
            return text
        if text.startswith(c):
            return text[len(c):]
        # Compare with spaces/punctuation REMOVED: the re-decode freely adds
        # them ("作業してたら完全に" -> "作業 してたら 完全 に", 。<->，), and
        # counting raw characters then re-emitted a tail ("ら 完全 に。") or
        # swallowed a head ("いました。").
        nc, _ = self._norm_map(c)
        nt, idx = self._norm_map(text)

        def after(n):            # raw index just past the first n normalized chars
            return idx[n - 1] + 1 if 0 < n <= len(idx) else (len(text) if n else 0)
        if nt.startswith(nc):
            cut = after(len(nc))
        else:
            # Content probe: the committed tail; take the occurrence ending
            # nearest to where the committed text should end.
            probe = nc[-10:]
            ends = []
            i = nt.find(probe) if probe else -1
            while i >= 0:
                ends.append(i + len(probe))
                i = nt.find(probe, i + 1)
            # Fallback: same audio ≈ same length, counted in normalized chars.
            n = min(ends, key=lambda e: abs(e - len(nc))) if ends else len(nc)
            cut = after(n)
        # An alignment seam can leave stray leading punctuation (never real
        # sentence starts) — drop it.
        return text[cut:].lstrip(self._NORM_SKIP)

    _NORM_SKIP = " 　。！？!?.，、,；;…・"
    _TAIL_ENDINGS = frozenset(("です", "ですね", "ですよね", "ですよ", "んです", "んですね",
                               "ます", "ました", "ね", "よ", "よね", "けど", "でしょう"))
    _BACKCHANNELS = frozenset((
        "うん", "うんうん", "うんうんうん", "はい", "はいはい", "ええ", "ああ", "えっと",
        "あの", "そう", "そうそう", "そうです", "そうですね", "ですね", "んです", "なるほど",
        "嗯", "嗯嗯", "对", "对对", "是的", "好的", "yeah", "yes", "ok", "okay", "right",
        "mhm", "uhhuh"))

    @classmethod
    def _norm_map(cls, s):
        """(s without spaces/punctuation, lowercased; raw index of each kept char)."""
        keep = [(i, ch.lower()) for i, ch in enumerate(s) if ch not in cls._NORM_SKIP]
        return "".join(ch for _, ch in keep), [i for i, _ in keep]

    def _on_recognized_stream(self, text, detected, is_final):
        """LocalAgreement-2 commit on the UNCOMMITTED TAIL: only tail text two
        consecutive partials agree on is committed (& translated), broken at the
        most natural point. Robust to the STT revising its tail AND its head, so
        sentences are neither duplicated nor dropped while you keep speaking."""
        self._recog_busy = False
        if not self._is_listening():
            return   # a late STT result after stop — ignore (don't re-dispatch/write)
        if detected:
            self._note_detected(detected)
        text = text or ""
        if is_final:
            # Flush only the not-yet-committed tail (aligned, never re-emitted).
            units, _ = self._split_scored(self._uncommitted_tail(text), True)
            for s in units:
                self._commit_stream_sentence(s)
            # The utterance is closed: reset its stream state HERE, in result
            # order — never at the next utterance's onset (see _vad_step).
            self._stream_committed = ""
            self._stream_last = ""
            self._stream_detected = "auto"
            self._det_hist = []   # vote within ONE utterance: a bilingual
            # conversation may switch language at every turn.
            self._set_caption_interim("")
            if self._is_listening():
                self.status_label.setText(tr("Listening", self._lang))
        else:
            tail = self._uncommitted_tail(text)
            last_tail = self._uncommitted_tail(self._stream_last) \
                if self._stream_last else ""
            self._stream_last = text
            stable = self._common_prefix(tail, last_tail)
            units, consumed = self._split_scored(stable, False)
            for s in units:
                self._commit_stream_sentence(s)
            self._stream_committed += stable[:consumed]
            self._set_caption_interim(tail[consumed:])
        if self._recog_pending:                    # next queued request, in order
            pcm, fin = self._recog_pending.pop(0)
            self._dispatch_recognize(pcm, fin)

    def _note_detected(self, detected):
        """Majority-vote the per-window language detections instead of trusting
        each one: SenseVoice re-detects on every ~360ms partial, and a single
        noisy window mis-detecting (zh<->ja on short audio) would flip the
        translation's source-language hint mid-sentence. The vote over the last
        5 windows keeps the hint stable; ties go to the most recent."""
        self._det_hist.append(detected)
        del self._det_hist[:-5]
        best = max(set(self._det_hist),
                   key=lambda d: (self._det_hist.count(d),
                                  len(self._det_hist) - 1 - self._det_hist[::-1].index(d)))
        self._stream_detected = best

    def _live_context(self):
        """Recent committed source lines as disambiguation context for the live
        translation (pronouns/terminology coherence across sentences). Capped to
        the last few lines / ~200 chars so the prompt stays small."""
        if not self._ctx_history:
            return ""
        ctx = " ".join(self._ctx_history[-3:])
        return ctx[-200:]

    # A committable unit must contain at least one WORD character (latin/digit,
    # kana, CJK, hangul, cyrillic, thai). SenseVoice happily emits bare
    # punctuation ("。", "?") for noise/short tails — showing and "translating"
    # those is pure clutter.
    _WORDLIKE = re.compile(r"[0-9A-Za-z぀-ヿ㐀-鿿"
                           r"가-힯Ѐ-ӿ฀-๿]")

    def _commit_stream_sentence(self, source):
        source = (source or "").strip()
        if not source or not self._WORDLIKE.search(source):
            return
        # A lone latin letter ("H.", "I.") or a single kana/CJK char ("の。",
        # "あ。") is noise or a filler SenseVoice split off — real sessions were
        # littered with them.
        if sum(ch.isalpha() for ch in source) < 2:
            return
        # A seam re-emitting the last few kana of the previous line
        # ("…分けてる。" then "けてる。") — drop it.
        ns, _ = self._norm_map(source)
        prev = self._ctx_history[-1] if getattr(self, "_ctx_history", None) else ""
        if len(ns) <= 4 and prev and self._norm_map(prev)[0].endswith(ns):
            return
        # A bare sentence ending ("ですね。", "んです。") after a line that was cut
        # mid-sentence is that line's TAIL ("…メンバーさんという|ですね"); it adds
        # nothing to the translation already sent — don't start the next line with it.
        if ns in self._TAIL_ENDINGS and prev and not prev.rstrip().endswith(
                tuple(self._SENT_END) + ("？", "！")):
            return
        # Backchannels ("うん。", "ですね。", "そうです。") got a line — and a
        # translation request — of their own; let them ride with the next line.
        if ns in self._BACKCHANNELS or len(ns) <= 3:
            self._held = (getattr(self, "_held", "") + source)[-40:]
            return
        if getattr(self, "_held", ""):
            source, self._held = self._held + source, ""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        # Unique per-sentence key: two sentences committed in the same second
        # used to share the "[HH:MM:SS]" key, so one translation overwrote the
        # other. The key's "#n" suffix is never displayed.
        self._seq = getattr(self, "_seq", 0) + 1
        key = f"{ts}#{self._seq}"
        self.input_text.insertPlainText(f"[{ts}] {source}\n")
        self.input_text.ensureCursorVisible()
        self._push_caption(source=source, key=key)
        # Reserve this sentence's output row now, so translations line up with
        # their sources in order even when replies come back out of order.
        self._out_rows.append([key, ""])
        self._render_output()
        # No context for very short/garbled lines: the model then "translates"
        # the context instead and the line gets content its source never had.
        ctx = self._live_context() if len(source) >= 12 else ""
        self._ctx_history.append(source)   # after building ctx (don't include self)
        del self._ctx_history[:-6]         # keep only the last few lines
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        api_key = load_api_key_for_model(model) if online else ""
        dst = get_language_code(self.target_combo.currentText())
        if online and backend.get_config("live_stream_translation", False):
            # Stream mode: grow the reserved row token-by-token.
            self._stream_text[key] = ""
            w = LiveTranslateStreamWorker(key, source, self._stream_detected, dst,
                                          model, online, api_key, context=ctx)
            w.chunk.connect(self._on_stream_chunk)
            w.done.connect(self._on_stream_done)
            w.finished.connect(lambda w=w: self._retire_local(w))
            self._local_workers.append(w)
            w.start()
            return
        w = LiveTranslateWorker(key, source, self._stream_detected, dst, model,
                                online, api_key, context=ctx)
        w.done.connect(self._on_translated_stream)
        w.finished.connect(lambda w=w: self._retire_local(w))
        self._local_workers.append(w)
        w.start()

    def _on_translated_stream(self, key, translated, tokens=0):
        self._session_tokens += int(tokens or 0)
        # A sentence that still failed after the retry is marked, not dropped.
        text = translated or "⚠ " + tr("Translation Failed", self._lang)
        self._set_out_row(key, text)
        self._push_caption(translated=text, key=key)

    def _render_output(self):
        """Rebuild the translation pane from the ordered rows (one per committed
        source sentence)."""
        self.output_text.setPlainText("".join(
            f"[{k.split('#')[0]}] {t}\n" for k, t in self._out_rows))
        from PySide6.QtGui import QTextCursor
        self.output_text.moveCursor(QTextCursor.End)
        self.output_text.ensureCursorVisible()

    def _set_out_row(self, key, text):
        for row in reversed(self._out_rows):
            if row[0] == key:
                row[1] = text
                self._render_output()
                return

    def _on_stream_chunk(self, key, text):
        self._stream_text[key] = text
        self._set_out_row(key, text)
        self._set_caption_interim(text)        # show the growing translation live

    def _on_stream_done(self, key, tokens=0):
        self._session_tokens += int(tokens or 0)   # streamed line's token cost
        final = self._stream_text.pop(key, "")
        if not final:
            final = "⚠ " + tr("Translation Failed", self._lang)
            self._set_out_row(key, final)
        self._push_caption(translated=final, key=key)
        self._set_caption_interim("")

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
                self._caption_bar.closed.connect(self._on_caption_closed)
            self._caption_bar.show_centered()
        elif self._caption_bar is not None:
            self._caption_bar.hide()

    def _on_caption_closed(self):
        """The bar's own × hid it — untick the toggle so one click reopens it."""
        if self.caption_btn.isChecked():
            self.caption_btn.blockSignals(True)
            self.caption_btn.setChecked(False)
            self.caption_btn.blockSignals(False)

    def _on_caption_destroyed(self, *args):
        self._caption_bar = None

    def _push_caption(self, source=None, translated=None, key=None):
        """Update the floating caption bar (if open) with the latest lines.
        ``key`` pairs a translation with ITS source sentence (local mode)."""
        bar = self._caption_bar
        if bar is None:
            return
        if source is not None:
            bar.set_source(source, key)
        if translated is not None:
            bar.set_translated(translated, key)

    def _set_caption_interim(self, text):
        """Show the live, not-yet-finalized source text in the caption bar."""
        if self._caption_bar is not None:
            self._caption_bar.set_interim(text)

    def _info(self, text, error=False, duration=4000):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Real-Time Voice", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=duration, parent=self)
