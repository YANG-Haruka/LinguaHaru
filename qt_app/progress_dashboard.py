"""Translation progress dashboard: a grid of metric cards.

Driven by the Translate page during a run. The page calls update_metrics()
with aggregated numbers; this widget formats and displays them. Elapsed / ETA /
speed are computed here from a monotonic start time + completed-line count.
"""

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QHBoxLayout

from qfluentwidgets import TitleLabel, PushButton, FluentIcon, CaptionLabel

from qt_app.i18n import tr
from qt_app.widgets import MetricCard, RingMetricCard


def _fmt_dur(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(int(n))


class ProgressDashboard(QWidget):
    def __init__(self, parent=None, lang="en", on_stop=None, on_open=None, on_back=None):
        super().__init__(parent)
        self.setObjectName("ProgressDashboard")
        self._lang = lang
        self._on_stop = on_stop
        self._on_open = on_open
        self._on_back = on_back
        self._start_time = None
        self._last_percent = 0.0
        # Tick the clock every second so Elapsed/ETA keep moving even during the
        # long opaque transcription phase (which emits no progress events).
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(16)

        head = QHBoxLayout()
        self.title = TitleLabel(tr("Task Progress", lang))
        head.addWidget(self.title)
        head.addStretch(1)
        self.stop_btn = PushButton(FluentIcon.CANCEL, tr("Stop Translation", lang))
        self.stop_btn.clicked.connect(lambda: self._on_stop and self._on_stop())
        head.addWidget(self.stop_btn)
        # Shown only after completion (metrics stay visible alongside).
        self.open_btn = PushButton(FluentIcon.FOLDER, tr("Open Output Folder", lang))
        self.open_btn.clicked.connect(lambda: self._on_open and self._on_open())
        self.open_btn.hide()
        head.addWidget(self.open_btn)
        self.back_btn = PushButton(FluentIcon.RETURN, tr("New Translation", lang))
        self.back_btn.clicked.connect(lambda: self._on_back and self._on_back())
        self.back_btn.hide()
        head.addWidget(self.back_btn)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        self.ring_card = RingMetricCard(tr("Task Progress", lang))
        grid.addWidget(self.ring_card, 0, 0, 2, 1)

        self.lines_card = MetricCard(tr("Line Stats", lang), "0", "",
                                     FluentIcon.ALIGNMENT, "#0d83d6")
        self.elapsed_card = MetricCard(tr("Elapsed Time", lang), "00:00", "",
                                       FluentIcon.HISTORY, "#7a5af5")
        self.eta_card = MetricCard(tr("Remaining Time", lang), "--:--", "",
                                   FluentIcon.STOP_WATCH, "#f59e0b")
        self.tokens_card = MetricCard(tr("Token Usage", lang), "0", "",
                                      FluentIcon.MARKET, "#16a34a")
        self.live_card = MetricCard(tr("Live Tasks", lang), "0", "",
                                    FluentIcon.SPEED_HIGH, "#0ea5e9")
        self.speed_card = MetricCard(tr("Average Speed", lang), "0",
                                     tr("lines/min", lang), FluentIcon.SPEED_MEDIUM, "#ec4899")
        self.failed_card = MetricCard(tr("Failed Requests", lang), "0", "",
                                      FluentIcon.CLOSE, "#ef4444")
        self.stability_card = MetricCard(tr("Task Stability", lang), "100%", "",
                                         FluentIcon.HEART, "#10b981")

        grid.addWidget(self.lines_card, 0, 1)
        grid.addWidget(self.elapsed_card, 0, 2)
        grid.addWidget(self.eta_card, 0, 3)
        grid.addWidget(self.tokens_card, 1, 1)
        grid.addWidget(self.live_card, 1, 2)
        grid.addWidget(self.speed_card, 1, 3)
        grid.addWidget(self.failed_card, 2, 1)
        grid.addWidget(self.stability_card, 2, 2)
        layout.addLayout(grid)

        # Live status line: the backend's per-segment desc (rate / ETA / tokens).
        self.status = CaptionLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self._cards = {
            "lines": (self.lines_card, "Line Stats"),
            "elapsed": (self.elapsed_card, "Elapsed Time"),
            "eta": (self.eta_card, "Remaining Time"),
            "tokens": (self.tokens_card, "Token Usage"),
            "live": (self.live_card, "Live Tasks"),
            "speed": (self.speed_card, "Average Speed"),
            "failed": (self.failed_card, "Failed Requests"),
            "stability": (self.stability_card, "Task Stability"),
        }

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Task Progress", lang))
        self.stop_btn.setText(tr("Stop Translation", lang))
        self.open_btn.setText(tr("Open Output Folder", lang))
        self.back_btn.setText(tr("New Translation", lang))
        self.ring_card.set_label(tr("Task Progress", lang))
        for card, key in self._cards.values():
            card.set_label(tr(key, lang))
        self.speed_card.sub.setText(tr("lines/min", lang))

    def set_status(self, text):
        self.status.setText(text or "")

    def start(self):
        self._start_time = time.monotonic()
        self._last_percent = 0.0
        self.ring_card.set_value(0)
        self.status.setText("")
        # Running state: stop visible, open/back hidden.
        self.stop_btn.show()
        self.open_btn.hide()
        self.back_btn.hide()
        self._clock.start()

    def show_done(self, summary, can_open):
        """Finished: keep all metrics on screen, swap Stop for Open/New."""
        self._clock.stop()
        self.stop_btn.hide()
        self.open_btn.setVisible(bool(can_open))
        self.back_btn.show()
        if summary:
            self.status.setText("✓ " + summary)

    def stop_clock(self):
        self._clock.stop()

    def _tick_clock(self):
        """1 Hz refresh of Elapsed (+ ETA from the last known percent), so the
        dashboard never looks frozen during phases with no progress events."""
        if not self._start_time:
            return
        elapsed = time.monotonic() - self._start_time
        self.elapsed_card.set_value(_fmt_dur(elapsed))
        frac = self._last_percent / 100.0
        if frac > 0.01:
            self.eta_card.set_value(_fmt_dur(elapsed * (1 - frac) / frac))

    def update_metrics(self, percent, total_files, done_files, live_tasks,
                       failed, total_tokens):
        """Refresh all cards. Speed/ETA/elapsed are derived from the start time
        and the fraction complete (files act as the 'line' unit at this layer)."""
        self._last_percent = percent
        if percent >= 100:
            self._clock.stop()
        self.ring_card.set_value(percent)
        self.lines_card.set_value(
            f"{done_files}/{total_files}",
            f"{tr('Completed', self._lang)} {done_files} · "
            f"{tr('Remaining', self._lang)} {max(0, total_files - done_files)}")
        elapsed = (time.monotonic() - self._start_time) if self._start_time else 0
        self.elapsed_card.set_value(_fmt_dur(elapsed))

        frac = percent / 100.0
        if frac > 0.01 and elapsed > 0:
            eta = elapsed * (1 - frac) / frac
            self.eta_card.set_value(_fmt_dur(eta))
            rate = (frac * total_files) / (elapsed / 60.0) if elapsed else 0
            self.speed_card.set_value(f"{rate:.1f}", tr("lines/min", self._lang))
        else:
            self.eta_card.set_value("--:--")

        self.tokens_card.set_value(_fmt_tokens(total_tokens))
        self.live_card.set_value(live_tasks)
        self.failed_card.set_value(failed)
        attempted = done_files + failed
        stability = 100 if attempted == 0 else int(100 * done_files / attempted)
        self.stability_card.set_value(f"{stability}%")
