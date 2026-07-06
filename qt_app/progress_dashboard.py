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
from core import sysmon


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
    def __init__(self, parent=None, lang="en", on_stop=None, on_open=None, on_back=None,
                 on_pause=None, on_resume=None):
        super().__init__(parent)
        self.setObjectName("ProgressDashboard")
        self._lang = lang
        self._on_stop = on_stop
        self._on_open = on_open
        self._on_back = on_back
        self._on_pause = on_pause
        self._on_resume = on_resume
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
        # Running: [暂停][停止].  Paused: [继续][返回].  Done: [打开][新翻译].
        self.pause_btn = PushButton(FluentIcon.PAUSE, tr("Pause", lang))
        self.pause_btn.clicked.connect(lambda: self._on_pause and self._on_pause())
        head.addWidget(self.pause_btn)
        self.resume_btn = PushButton(FluentIcon.PLAY, tr("Resume", lang))
        self.resume_btn.clicked.connect(lambda: self._on_resume and self._on_resume())
        self.resume_btn.hide()
        head.addWidget(self.resume_btn)
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
        grid.addWidget(self.ring_card, 0, 0, 3, 1)   # spans all 3 rows on the left

        # 9 metric cards in a 3x3 grid beside the ring. Progress/throughput on top,
        # then the cumulative + thread info, then live system status + which device
        # (GPU/CPU) the heavy work runs on. (Dropped the old Failed/Stability cards.)
        self.lines_card = MetricCard(tr("Files", lang), "0", "",
                                     FluentIcon.ALIGNMENT, "#0d83d6")
        self.elapsed_card = MetricCard(tr("Elapsed Time", lang), "00:00", "",
                                       FluentIcon.HISTORY, "#7a5af5")
        self.eta_card = MetricCard(tr("Remaining Time", lang), "--:--", "",
                                   FluentIcon.STOP_WATCH, "#f59e0b")
        self.speed_card = MetricCard(tr("Average Speed", lang), "—",
                                     tr("lines/min", lang), FluentIcon.SPEED_MEDIUM, "#ec4899")
        self.threads_card = MetricCard(tr("Thread Count", lang), "0", "",
                                       FluentIcon.SPEED_HIGH, "#0ea5e9")
        self.tokens_card = MetricCard(tr("Token Usage", lang), "0", "",
                                      FluentIcon.MARKET, "#16a34a")
        self.cpu_card = MetricCard(tr("CPU Usage", lang), "—", "",
                                   FluentIcon.IOT, "#0ea5e9")
        self.gpu_card = MetricCard(tr("GPU Usage", lang), "—", "",
                                   FluentIcon.GAME, "#a855f7")
        self.hw_card = MetricCard(tr("Compute Device", lang), "—", "",
                                  FluentIcon.DEVELOPER_TOOLS, "#10b981")

        grid.addWidget(self.lines_card, 0, 1)
        grid.addWidget(self.elapsed_card, 0, 2)
        grid.addWidget(self.eta_card, 0, 3)
        grid.addWidget(self.speed_card, 1, 1)
        grid.addWidget(self.threads_card, 1, 2)
        grid.addWidget(self.tokens_card, 1, 3)
        grid.addWidget(self.cpu_card, 2, 1)
        grid.addWidget(self.gpu_card, 2, 2)
        grid.addWidget(self.hw_card, 2, 3)
        layout.addLayout(grid)

        # Live status line: the backend's per-segment desc (rate / ETA / tokens).
        self.status = CaptionLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # Coverage line (shown only after completion): what got translated by
        # content category + how many segments fell back to the original.
        self.coverage = CaptionLabel("")
        self.coverage.setWordWrap(True)
        self.coverage.hide()
        layout.addWidget(self.coverage)
        # Quality-check warnings (shown only after completion, if any).
        self.qa = CaptionLabel("")
        self.qa.setWordWrap(True)
        self.qa.setStyleSheet("color:#d08700;")
        self.qa.hide()
        layout.addWidget(self.qa)
        layout.addStretch(1)

        self._cards = {
            "lines": (self.lines_card, "Files"),
            "elapsed": (self.elapsed_card, "Elapsed Time"),
            "eta": (self.eta_card, "Remaining Time"),
            "speed": (self.speed_card, "Average Speed"),
            "threads": (self.threads_card, "Thread Count"),
            "tokens": (self.tokens_card, "Token Usage"),
            "cpu": (self.cpu_card, "CPU Usage"),
            "gpu": (self.gpu_card, "GPU Usage"),
            "hw": (self.hw_card, "Compute Device"),
        }

        # Poll live CPU/GPU usage every 2s while a task runs.
        self._sysmon = QTimer(self)
        self._sysmon.setInterval(2000)
        self._sysmon.timeout.connect(self._tick_sysmon)

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Task Progress", lang))
        self.pause_btn.setText(tr("Pause", lang))
        self.resume_btn.setText(tr("Resume", lang))
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
        self.speed_card.set_value("—", tr("lines/min", self._lang))
        # Compute device (GPU/CPU) — set once; tells the user where the heavy work
        # (transcription/translation) actually runs. Critical for GPU users.
        hw = sysmon.hardware_summary()
        self.hw_card.set_value(
            tr("GPU", self._lang) if hw.get("gpu") else tr("CPU", self._lang),
            (hw.get("name") or hw.get("detail") or "")[:28])
        self._tick_sysmon()        # prime CPU% + first reading
        self._sysmon.start()
        # Running state: [暂停][停止] visible, resume/open/back hidden.
        self.pause_btn.show()
        self.resume_btn.hide()
        self.stop_btn.show()
        self.open_btn.hide()
        self.back_btn.hide()
        self._clock.start()

    def _tick_sysmon(self):
        """Refresh the live CPU/GPU usage cards (best-effort)."""
        u = sysmon.usage()
        self.cpu_card.set_value("—" if u.get("cpu") is None else f"{u['cpu']:.0f}%")
        if u.get("gpu") is None:
            self.gpu_card.set_value("—")
        else:
            mem = ""
            if u.get("gpu_mem_total"):
                mem = f"{u['gpu_mem_used']}/{u['gpu_mem_total']} MB"
            self.gpu_card.set_value(f"{u['gpu']:.0f}%", mem)

    def set_paused(self, paused):
        """Toggle the dashboard between running ([暂停][停止]) and paused
        ([继续][返回]). Paused freezes the clock display; the backend is blocked
        in place, so resume continues from the exact point."""
        if paused:
            self.pause_btn.hide()
            self.stop_btn.hide()
            self.resume_btn.show()
            self.back_btn.setText(tr("Back", self._lang))
            self.back_btn.show()
            self.status.setText(tr("Paused", self._lang))
            self._clock.stop()
            self._sysmon.stop()
        else:
            self.resume_btn.hide()
            self.back_btn.hide()
            self.pause_btn.show()
            self.stop_btn.show()
            if self._start_time:
                self._clock.start()
                self._sysmon.start()

    # QA check key -> i18n label key.
    _QA_LABELS = {
        "placeholders": "Placeholder mismatch", "length_ratio": "Length anomaly",
        "subtitle_length": "Subtitle line too wide", "subtitle_lines": "Subtitle >2 lines",
        "subtitle_cps": "Reading speed too fast", "glossary_terms": "Glossary term not applied",
    }

    def show_done(self, summary, can_open, coverage=None, qa=None):
        """Finished: keep all metrics on screen, swap Stop for Open/New."""
        self._clock.stop()
        self._sysmon.stop()
        self.pause_btn.hide()
        self.resume_btn.hide()
        self.stop_btn.hide()
        self.open_btn.setVisible(bool(can_open))
        self.back_btn.setText(tr("New Translation", self._lang))
        self.back_btn.show()
        if summary:
            self.status.setText("✓ " + summary)
        self._show_coverage(coverage)
        self._show_qa(qa)

    def _show_qa(self, qa):
        """One-line quality-warning summary: '质量提示: 占位符不一致 3 · 阅读速度过快 12'."""
        if not qa:
            self.qa.hide()
            return
        parts = []
        for k, n in qa.items():
            cnt = len(n) if isinstance(n, (list, dict)) else n
            if cnt:
                parts.append(f"{tr(self._QA_LABELS.get(k, k), self._lang)} {cnt}")
        if not parts:
            self.qa.hide()
            return
        self.qa.setText(f"{tr('Quality warnings', self._lang)}: " + " · ".join(parts))
        self.qa.show()

    def _show_coverage(self, coverage):
        """Compact coverage line: '翻译覆盖：正文 80 · 表格 20 · 0 未翻译'."""
        if not coverage or not coverage.get("total"):
            self.coverage.hide()
            return
        parts = [f"{tr(cat, self._lang)} {n}" for cat, n in coverage.get("by_category", {}).items() if n]
        parts.append(f"{coverage.get('fallback', 0)} {tr('Untranslated', self._lang)}")
        if coverage.get("needs_review"):
            parts.append(f"{coverage['needs_review']} {tr('Needs review', self._lang)}")
        self.coverage.setText(
            f"{tr('Translation Coverage', self._lang)}: " + " · ".join(parts))
        self.coverage.show()

    def stop_clock(self):
        self._clock.stop()
        self._sysmon.stop()

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

    def update_metrics(self, percent, total_files, done_files, thread_count,
                       failed, total_tokens, prompt_tokens=0, completion_tokens=0,
                       model=None):
        """Refresh the metric cards. (failed is accepted for backward-compat but
        no longer shown.) Cost is estimated from the exact prompt/completion split
        when available, else approximated from the live total."""
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
        # ETA + speed only once there's a meaningful sample (>=5s and >2%); else
        # show "—" instead of a wildly wrong early number (e.g. 147.8 lines/min).
        if frac > 0.02 and elapsed >= 5:
            self.eta_card.set_value(_fmt_dur(elapsed * (1 - frac) / frac))
            rate = (frac * total_files) / (elapsed / 60.0)
            self.speed_card.set_value(f"{rate:.1f}", tr("lines/min", self._lang))
        else:
            self.eta_card.set_value("--:--")
            self.speed_card.set_value("—", tr("lines/min", self._lang))

        # 累积消耗: exact token count + estimated cost (¥/＄/￥ by UI language).
        self.tokens_card.set_value(_fmt_tokens(total_tokens),
                                   self._cost_sub(total_tokens, prompt_tokens,
                                                  completion_tokens, model))
        self.threads_card.set_value(thread_count)

    def _cost_sub(self, total_tokens, prompt_tokens, completion_tokens, model):
        """'12,345 tokens · ≈¥0.05' — exact tokens + estimated cost. Uses the exact
        prompt/completion split when known, otherwise a 50/50 approximation of the
        live total so a cost shows during the run too."""
        sub = f"{int(total_tokens):,} tokens"
        if not model:
            return sub
        p, c = int(prompt_tokens or 0), int(completion_tokens or 0)
        if p == 0 and c == 0 and total_tokens:        # live: approximate the split
            p = c = int(total_tokens) // 2
        if p == 0 and c == 0:
            return sub
        try:
            from core.pricing import estimate_cost
            amt, symbol, _ccy = estimate_cost(model, p, c, self._lang)
            return f"{sub} · ≈{symbol}{amt:.4f}"
        except Exception:  # noqa: BLE001 — cost is best-effort
            return sub
