"""Settings page: every change is persisted to system_config.json immediately.

Structure mirrors the Web settings exactly: four collapsible cards —
Run Mode (LAN) · Translation Options · Data & Storage · Model Management.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QFileDialog,
    QLineEdit, QApplication, QLabel,
)

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, CaptionLabel,
    CardWidget, PushButton, LineEdit, FluentIcon, MessageBox, ComboBox,
    ToolTipFilter, ToolTipPosition,
)

from core import backend
from core import model_store
from qt_app.i18n import tr

# Outlined button styles: a neutral one and a red "danger" one (for the
# irreversible delete), both with a hover fill — cleaner than solid blocks.
_NEUTRAL_BTN_QSS = (
    "PushButton{border:1px solid rgba(128,128,128,0.40);border-radius:8px;"
    "padding:5px 16px;background:transparent;}"
    "PushButton:hover{background:rgba(128,128,128,0.14);border-color:rgba(128,128,128,0.65);}"
    "PushButton:pressed{background:rgba(128,128,128,0.22);}"
)
_DANGER_BTN_QSS = (
    "PushButton{border:1px solid rgba(224,80,60,0.55);border-radius:8px;"
    "padding:5px 16px;background:transparent;color:#e0503c;}"
    "PushButton:hover{background:rgba(224,80,60,0.16);border-color:#e0503c;}"
    "PushButton:pressed{background:rgba(224,80,60,0.28);}"
)


class _CollapsibleCard(CardWidget):
    """A card whose body is hidden until its header row is clicked (so long
    sections fold away by default). Add content via .body (a QVBoxLayout).
    The header uses theme-aware labels so the title stays legible in any theme."""

    def __init__(self, title, expanded=False, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QWidget()
        self._header.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(20, 14, 20, 14)
        hl.setSpacing(8)
        self._arrow = StrongBodyLabel("▾" if expanded else "▸")
        self._title = StrongBodyLabel(title)
        hl.addWidget(self._arrow)
        hl.addWidget(self._title)
        hl.addStretch(1)
        self._header.mousePressEvent = self._toggle    # whole row is clickable
        root.addWidget(self._header)

        self._bodyw = QWidget()
        self.body = QVBoxLayout(self._bodyw)
        self.body.setContentsMargins(20, 0, 20, 16)
        self.body.setSpacing(10)
        self._bodyw.setVisible(expanded)
        root.addWidget(self._bodyw)

    def _toggle(self, _event=None):
        self._expanded = not self._expanded
        self._bodyw.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")

    def set_title(self, title):
        self._title.setText(title)


class _SubSection(QWidget):
    """A lightweight nested collapsible (no card chrome) for grouping sub-options
    INSIDE a _CollapsibleCard — mirrors the web's nested <details>. Add content
    via .body (a QVBoxLayout)."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)
        self._header = QWidget()
        self._header.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(0, 6, 0, 6)
        hl.setSpacing(8)
        self._arrow = BodyLabel("▸")
        self._title = StrongBodyLabel(title)
        hl.addWidget(self._arrow)
        hl.addWidget(self._title)
        hl.addStretch(1)
        self._header.mousePressEvent = self._toggle
        root.addWidget(self._header)
        self._bodyw = QWidget()
        self.body = QVBoxLayout(self._bodyw)
        self.body.setContentsMargins(16, 0, 0, 8)   # indent so nesting reads visually
        self.body.setSpacing(10)
        self._bodyw.setVisible(False)
        root.addWidget(self._bodyw)

    def _toggle(self, _event=None):
        vis = not self._bodyw.isVisible()
        self._bodyw.setVisible(vis)
        self._arrow.setText("▾" if vis else "▸")

    def set_title(self, title):
        self._title.setText(title)


class SettingsPage(ScrollArea):
    """Run Mode (LAN), Translation Options, Data & Storage, Model Management —
    same set/order as the Web settings."""

    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self._lang = lang
        self._web_proc = None          # the LAN web-server subprocess (if running)
        self._lan_port = 8080
        # Set by MainWindow so the language selector can drive a global retranslate.
        self.on_ui_lang_changed = None
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        container = QWidget()
        container.setObjectName("settingsScrollContainer")
        container.setStyleSheet(
            "#settingsScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        config = backend.read_config()

        self.section_translation = StrongBodyLabel(tr("Settings", lang))
        layout.addWidget(self.section_translation)
        self.per_model_hint = BodyLabel(tr("Per Model Hint", lang))
        self.per_model_hint.setWordWrap(True)
        layout.addWidget(self.per_model_hint)

        # --- Card 1: Run Mode (LAN web access) ---
        self.card_run = _CollapsibleCard(tr("Run Mode", lang))
        layout.addWidget(self.card_run)
        self.lan_switch = SwitchButton()
        self.lan_switch.setChecked(False)      # session-bound; server isn't running yet
        self.lan_switch.checkedChanged.connect(self._toggle_lan)
        self.lan_label = BodyLabel(tr("LAN Mode", lang))
        lan_row = QHBoxLayout()
        lan_row.addWidget(self.lan_label, 1)
        lan_row.addWidget(self.lan_switch)
        self.card_run.body.addLayout(lan_row)
        self.lan_hint = CaptionLabel(tr("LAN access hint", lang))
        self.lan_hint.setWordWrap(True)
        self.card_run.body.addWidget(self.lan_hint)
        self.lan_url = BodyLabel("")
        self.lan_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lan_url.setStyleSheet("font-weight:600;")
        self.card_run.body.addWidget(self.lan_url)
        self.lan_admin_label = BodyLabel(tr("LAN admin password", lang))
        self.lan_admin_edit = LineEdit()
        self.lan_admin_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.lan_admin_edit.setPlaceholderText(
            "已设置（留空则不修改）" if config.get("lan_admin_password_hash") else "留空则不启用")
        self.lan_admin_edit.editingFinished.connect(self._save_lan_admin)
        admin_row = QHBoxLayout()
        admin_row.addWidget(self.lan_admin_label)
        admin_row.addWidget(self.lan_admin_edit, 1)
        self.card_run.body.addLayout(admin_row)

        # --- Card 2: Translation Options ---
        # Three tiers: common (this card), Advanced, and Real-Time Voice — so the
        # frequently-used options stay visible and the rest fold away.
        self.card_options = _CollapsibleCard(tr("Translation Options", lang))
        layout.addWidget(self.card_options)
        gl_form = QFormLayout()          # COMMON
        gl_form.setSpacing(12)
        adv_form = QFormLayout()         # ADVANCED (tone/length/style + glossary/dedup/type-ctx)
        adv_form.setSpacing(12)
        live_form = QFormLayout()        # REAL-TIME VOICE (stream + VAD tuning)
        live_form.setSpacing(12)
        # Translation mode (precise / natural / polish / subtitle)
        self._modes = []
        self.mode_combo = ComboBox()
        try:
            from core.translation_modes import load_modes, get_active_mode
            self._modes = list(load_modes().items())
            cur = get_active_mode()
            _zh = str(lang).startswith("zh")
            for i, (mid, m) in enumerate(self._modes):
                self.mode_combo.addItem(m.get("label", mid) if _zh
                                        else m.get("label_en", m.get("label", mid)))
                if mid == cur:
                    self.mode_combo.setCurrentIndex(i)
        except Exception:  # noqa: BLE001
            pass
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_label = BodyLabel(tr("Translation Mode", lang))
        gl_form.addRow(self.mode_label, self.mode_combo)
        self.auto_glossary = SwitchButton()
        self.auto_glossary.setChecked(config.get("auto_extract_glossary", False))
        self.auto_glossary.checkedChanged.connect(
            lambda v: backend.set_config("auto_extract_glossary", v))
        self.auto_glossary_label = BodyLabel(tr("AI Glossary Extraction", lang))
        adv_form.addRow(self.auto_glossary_label, self.auto_glossary)
        self.mask_ph = SwitchButton()
        self.mask_ph.setChecked(config.get("mask_placeholders", True))
        self.mask_ph.checkedChanged.connect(
            lambda v: backend.set_config("mask_placeholders", v))
        self.mask_ph_label = BodyLabel(tr("Placeholder Protection", lang))
        gl_form.addRow(self.mask_ph_label, self.mask_ph)
        self.dedup_ctx = SwitchButton()
        self.dedup_ctx.setChecked(config.get("dedup_context", False))
        self.dedup_ctx.checkedChanged.connect(
            lambda v: backend.set_config("dedup_context", v))
        self.dedup_ctx_label = BodyLabel(tr("Context-aware Dedup", lang))
        adv_form.addRow(self.dedup_ctx_label, self.dedup_ctx)
        self.with_ctx = SwitchButton()
        self.with_ctx.setChecked(config.get("translate_with_context", False))
        self.with_ctx.checkedChanged.connect(
            lambda v: backend.set_config("translate_with_context", v))
        self.with_ctx_label = BodyLabel(tr("Type Context", lang))
        adv_form.addRow(self.with_ctx_label, self.with_ctx)
        # Advanced modifiers: tone / length / free-text style guide.
        self._tones = [("", tr("Default", lang)), ("formal", tr("Formal", lang)),
                       ("casual", tr("Casual", lang))]
        self.tone_combo = ComboBox()
        for _v, lbl in self._tones:
            self.tone_combo.addItem(lbl)
        cur_tone = config.get("translation_tone", "")
        for i, (v, _l) in enumerate(self._tones):
            if v == cur_tone:
                self.tone_combo.setCurrentIndex(i)
        self.tone_combo.currentIndexChanged.connect(
            lambda i: backend.set_config("translation_tone", self._tones[i][0]) if 0 <= i < len(self._tones) else None)
        self.tone_label = BodyLabel(tr("Tone", lang))
        adv_form.addRow(self.tone_label, self.tone_combo)
        self._lengths = [("", tr("Default", lang)), ("keep", tr("Keep Length", lang)),
                         ("expand", tr("Allow Longer", lang)), ("short", tr("Concise", lang))]
        self.length_combo = ComboBox()
        for _v, lbl in self._lengths:
            self.length_combo.addItem(lbl)
        cur_len = config.get("translation_length", "")
        for i, (v, _l) in enumerate(self._lengths):
            if v == cur_len:
                self.length_combo.setCurrentIndex(i)
        self.length_combo.currentIndexChanged.connect(
            lambda i: backend.set_config("translation_length", self._lengths[i][0]) if 0 <= i < len(self._lengths) else None)
        self.length_label = BodyLabel(tr("Length", lang))
        adv_form.addRow(self.length_label, self.length_combo)
        self.style_edit = LineEdit()
        self.style_edit.setText(config.get("translation_style", ""))
        self.style_edit.setClearButtonEnabled(True)
        self.style_edit.editingFinished.connect(
            lambda: backend.set_config("translation_style", self.style_edit.text().strip()))
        self.style_label = BodyLabel(tr("Style Guide", lang))
        adv_form.addRow(self.style_label, self.style_edit)
        # Bilingual: bold + color the translated text so it stands out (subtitles).
        self.bi_bold = SwitchButton()
        self.bi_bold.setChecked(config.get("bilingual_bold", True))
        self.bi_bold.checkedChanged.connect(
            lambda v: backend.set_config("bilingual_bold", v))
        self.bi_bold_label = BodyLabel(tr("Bilingual Bold", lang))
        gl_form.addRow(self.bi_bold_label, self.bi_bold)
        self._bi_colors = [("", tr("None", lang)), ("C00000", tr("Red", lang)),
                           ("1F4E79", tr("Blue", lang)), ("2E7D32", tr("Green", lang)),
                           ("B36B00", tr("Orange", lang))]
        self.bi_color = ComboBox()
        for _hex, label in self._bi_colors:
            self.bi_color.addItem(label)
        cur_col = str(config.get("bilingual_color", "") or "")
        for i, (h, _l) in enumerate(self._bi_colors):
            if h == cur_col:
                self.bi_color.setCurrentIndex(i)
                break
        self.bi_color.currentIndexChanged.connect(
            lambda idx: backend.set_config("bilingual_color", self._bi_colors[idx][0]))
        self.bi_color_label = BodyLabel(tr("Translation Color", lang))
        gl_form.addRow(self.bi_color_label, self.bi_color)
        self.live_stream = SwitchButton()
        self.live_stream.setChecked(config.get("live_stream_translation", False))
        self.live_stream.checkedChanged.connect(
            lambda v: backend.set_config("live_stream_translation", v))
        self.live_stream_label = BodyLabel(tr("Stream Translation", lang))
        live_form.addRow(self.live_stream_label, self.live_stream)
        # Real-time caption sentence-splitting: how long a pause ends an
        # utterance. Slower speakers need a longer pause so their natural
        # between-phrase gaps don't chop a sentence into fragments.
        self._hangs = [("600", tr("Hang Sensitive", lang)), ("900", tr("Hang Standard", lang)),
                       ("1200", tr("Hang Relaxed", lang)), ("1600", tr("Hang Very Relaxed", lang))]
        self.hang_combo = ComboBox()
        for _v, lbl in self._hangs:
            self.hang_combo.addItem(lbl)
        cur_hang = str(config.get("live_vad_hang_ms", 900))
        for i, (v, _l) in enumerate(self._hangs):
            if v == cur_hang:
                self.hang_combo.setCurrentIndex(i)
        self.hang_combo.currentIndexChanged.connect(
            lambda i: backend.set_config("live_vad_hang_ms", int(self._hangs[i][0])) if 0 <= i < len(self._hangs) else None)
        self.hang_label = BodyLabel(tr("Segmentation Pause", lang))
        live_form.addRow(self.hang_label, self.hang_combo)
        # Mic sensitivity (onset / neural-VAD threshold).
        self._sens = [("high", tr("Sens High", lang)), ("standard", tr("Sens Standard", lang)),
                      ("low", tr("Sens Low", lang))]
        self.sens_combo = ComboBox()
        for _v, lbl in self._sens:
            self.sens_combo.addItem(lbl)
        cur_sens = config.get("live_vad_sensitivity", "standard")
        for i, (v, _l) in enumerate(self._sens):
            if v == cur_sens:
                self.sens_combo.setCurrentIndex(i)
        self.sens_combo.currentIndexChanged.connect(
            lambda i: backend.set_config("live_vad_sensitivity", self._sens[i][0]) if 0 <= i < len(self._sens) else None)
        self.sens_label = BodyLabel(tr("Mic Sensitivity", lang))
        live_form.addRow(self.sens_label, self.sens_combo)
        # Force-cut ceiling: hard cap on one utterance's length.
        self._maxsegs = [("15000", tr("MaxSeg 15s", lang)), ("30000", tr("MaxSeg 30s", lang)),
                         ("60000", tr("MaxSeg 60s", lang))]
        self.maxseg_combo = ComboBox()
        for _v, lbl in self._maxsegs:
            self.maxseg_combo.addItem(lbl)
        cur_max = str(config.get("live_vad_max_seg_ms", 30000))
        for i, (v, _l) in enumerate(self._maxsegs):
            if v == cur_max:
                self.maxseg_combo.setCurrentIndex(i)
        self.maxseg_combo.currentIndexChanged.connect(
            lambda i: backend.set_config("live_vad_max_seg_ms", int(self._maxsegs[i][0])) if 0 <= i < len(self._maxsegs) else None)
        self.maxseg_label = BodyLabel(tr("Force Cut", lang))
        live_form.addRow(self.maxseg_label, self.maxseg_combo)
        self.card_options.body.addLayout(gl_form)
        # Advanced + Real-Time Voice are NESTED sub-sections inside this card
        # (fold open the card, then each sub-section) — mirrors the web layout.
        self.card_advanced = _SubSection(tr("Advanced Options", lang))
        self.card_advanced.body.addLayout(adv_form)
        self.card_options.body.addWidget(self.card_advanced)
        self.card_live = _SubSection(tr("Real-Time Voice", lang))
        self.card_live.body.addLayout(live_form)
        self.card_options.body.addWidget(self.card_live)

        # --- Card 3: Data & Storage (output folder + history retention/clear) ---
        self.card_data = _CollapsibleCard(tr("Data & Storage", lang))
        layout.addWidget(self.card_data)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.output_label = BodyLabel(tr("Output Folder", lang))
        self.output_edit = LineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(config.get("result_dir", "result"))
        self.output_browse = PushButton(FluentIcon.FOLDER, tr("Browse", lang))
        self.output_browse.clicked.connect(self._pick_output_dir)
        out_row.addWidget(self.output_label)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.output_browse)
        self.card_data.body.addLayout(out_row)
        # Retention, grouped: RESULTS first (count / MB / days), then LOGS
        # (count / MB / days). 0 = unlimited. "Result count/days" reuse the
        # history-record limits (one record per result); "Result MB" caps the
        # output files on disk.
        hist_form = QFormLayout()
        hist_form.setSpacing(12)
        self.hist_max_edit = LineEdit()
        self.hist_max_edit.setText(str(config.get("history_max_records", 1000)))
        self.hist_max_edit.editingFinished.connect(self._save_hist_max)
        self.hist_max_label = BodyLabel(tr("Result Max Count", lang))
        self.result_size_edit = LineEdit()
        self.result_size_edit.setText(str(config.get("result_max_size_mb", 5000)))
        self.result_size_edit.editingFinished.connect(self._save_result_size)
        self.result_size_label = BodyLabel(tr("Result Max Size", lang))
        self.hist_age_edit = LineEdit()
        self.hist_age_edit.setText(str(config.get("history_max_age_days", 0)))
        self.hist_age_edit.editingFinished.connect(self._save_hist_age)
        self.hist_age_label = BodyLabel(tr("Result Max Days", lang))
        self.log_max_edit = LineEdit()
        self.log_max_edit.setText(str(config.get("log_max_files", 500)))
        self.log_max_edit.editingFinished.connect(self._save_log_max)
        self.log_max_label = BodyLabel(tr("Log Max Files", lang))
        self.log_size_edit = LineEdit()
        self.log_size_edit.setText(str(config.get("log_max_size_mb", 500)))
        self.log_size_edit.editingFinished.connect(self._save_log_size)
        self.log_size_label = BodyLabel(tr("Log Max Size", lang))
        self.log_age_edit = LineEdit()
        self.log_age_edit.setText(str(config.get("log_max_age_days", 30)))
        self.log_age_edit.editingFinished.connect(self._save_log_age)
        self.log_age_label = BodyLabel(tr("Log Max Days", lang))
        for lbl, edit in (
            (self.hist_max_label, self.hist_max_edit),
            (self.result_size_label, self.result_size_edit),
            (self.hist_age_label, self.hist_age_edit),
            (self.log_max_label, self.log_max_edit),
            (self.log_size_label, self.log_size_edit),
            (self.log_age_label, self.log_age_edit),
        ):
            hist_form.addRow(lbl, edit)
        self.card_data.body.addLayout(hist_form)
        # Two danger actions, side by side: "clear records" is the lighter one
        # (neutral outline), "clear records + files" is the irreversible one
        # (red outline that fills on hover). Content-width, not full-width blocks.
        hist_btn_row = QHBoxLayout()
        hist_btn_row.setSpacing(10)
        self.hist_clear_btn = PushButton(FluentIcon.BROOM, tr("Clear History", lang))
        self.hist_clear_btn.clicked.connect(self._clear_history)
        self.hist_clear_btn.setStyleSheet(_NEUTRAL_BTN_QSS)
        self.hist_clear_files_btn = PushButton(FluentIcon.DELETE, tr("Clear History And Files", lang))
        self.hist_clear_files_btn.clicked.connect(self._clear_history_and_files)
        self.hist_clear_files_btn.setStyleSheet(_DANGER_BTN_QSS)
        hist_btn_row.addWidget(self.hist_clear_btn)
        hist_btn_row.addWidget(self.hist_clear_files_btn)
        hist_btn_row.addStretch(1)
        self.card_data.body.addLayout(hist_btn_row)

        # --- Card 4: Model Management ---
        self.card_models = _CollapsibleCard(tr("Model Management", lang))
        layout.addWidget(self.card_models)
        loc_row = QHBoxLayout()
        loc_row.setSpacing(8)
        self.models_loc_label = BodyLabel(tr("Model Location", lang))
        loc_row.addWidget(self.models_loc_label)
        self.models_dir_edit = LineEdit()
        self.models_dir_edit.setReadOnly(True)
        loc_row.addWidget(self.models_dir_edit, 1)
        self.models_browse = PushButton(FluentIcon.FOLDER, tr("Change Location", lang))
        self.models_browse.clicked.connect(self._change_models_dir)
        loc_row.addWidget(self.models_browse)
        self.card_models.body.addLayout(loc_row)
        # Per-model install / delete / use, grouped by model type. OCR uses the
        # "Image OCR" plugin, STT the "Video/Audio" plugin (video-subtitle STT).
        self._model_workers = []
        self.ocr_header, self.ocr_body, self._ocr_body_layout = \
            self._make_model_section(tr("Image OCR Model", self._lang), "Image OCR Model Tip")
        self.card_models.body.addWidget(self.ocr_header)
        self.card_models.body.addWidget(self.ocr_body)
        self.stt_header, self.stt_body, self._stt_body_layout = \
            self._make_model_section(tr("Speech-to-Text Model", self._lang), "Speech-to-Text Model Tip")
        self.card_models.body.addWidget(self.stt_header)
        self.card_models.body.addWidget(self.stt_body)
        self.stt_scope_hint = CaptionLabel(tr("STT Scope Hint", self._lang))
        self.stt_scope_hint.setWordWrap(True)
        self.card_models.body.addWidget(self.stt_scope_hint)
        self.stt_hint = CaptionLabel(tr("Whisper Hint", self._lang))
        self.stt_hint.setWordWrap(True)
        self.card_models.body.addWidget(self.stt_hint)
        self._refresh_models()

        self._apply_tips()
        layout.addStretch(1)

        # Stop the LAN server cleanly when the app quits.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_web_server)

    # --- LAN web access (Qt launches the FastAPI server bound to 0.0.0.0) ---
    def _toggle_lan(self, on):
        if on:
            self._start_web_server()
        else:
            self._stop_web_server()

    def _start_web_server(self):
        import subprocess
        import sys
        if self._web_proc is not None and self._web_proc.poll() is None:
            return
        backend.set_config("lan_mode", True)   # makes the server bind 0.0.0.0
        env = dict(os.environ)
        env["PORT"] = str(self._lan_port)
        try:
            self._web_proc = subprocess.Popen(
                [sys.executable, "-m", "webapp.server"],
                cwd=backend.REPO_ROOT, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            backend.set_config("lan_mode", False)
            self.lan_url.setText("启动失败：" + str(e)[:120])
            return
        self.lan_url.setText(tr("LAN starting", self._lang))
        # Poll until the port answers (server load takes a few seconds).
        self._lan_tries = 0
        self._lan_timer = QTimer(self)
        self._lan_timer.timeout.connect(self._check_lan_up)
        self._lan_timer.start(1000)

    def _check_lan_up(self):
        self._lan_tries += 1
        if self._web_proc is None or self._web_proc.poll() is not None:
            self._lan_timer.stop()
            self.lan_url.setText("启动失败（端口可能被占用）")
            return
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:  # noqa: BLE001
            ip = "127.0.0.1"
        finally:
            s.close()
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(0.5)
        up = c.connect_ex(("127.0.0.1", self._lan_port)) == 0
        c.close()
        if up:
            self._lan_timer.stop()
            self.lan_url.setText(f"http://{ip}:{self._lan_port}")
        elif self._lan_tries >= 20:
            self._lan_timer.stop()
            self.lan_url.setText("启动超时，请检查端口或防火墙")

    def _stop_web_server(self):
        backend.set_config("lan_mode", False)
        if self._web_proc is not None and self._web_proc.poll() is None:
            self._web_proc.terminate()
            try:
                self._web_proc.wait(5)
            except Exception:  # noqa: BLE001
                self._web_proc.kill()
        self._web_proc = None
        if hasattr(self, "lan_url"):
            self.lan_url.setText("")

    def _save_lan_admin(self):
        v = self.lan_admin_edit.text()
        if v.strip():                          # set a new password (empty = no change)
            backend.set_config("lan_admin_password_hash", backend.hash_lan_password(v))
            self.lan_admin_edit.clear()
            self.lan_admin_edit.setPlaceholderText("已设置（留空则不修改）")

    # --- data & storage handlers ---
    def _save_hist_max(self):
        try:
            v = max(0, int(self.hist_max_edit.text().strip() or "0"))
        except ValueError:
            v = 1000
            self.hist_max_edit.setText("1000")
        backend.set_config("history_max_records", v)

    def _save_hist_age(self):
        try:
            v = max(0, int(self.hist_age_edit.text().strip() or "0"))
        except ValueError:
            v = 0
            self.hist_age_edit.setText("0")
        backend.set_config("history_max_age_days", v)

    def _save_int_config(self, edit, key, default):
        try:
            v = max(0, int(edit.text().strip() or "0"))
        except ValueError:
            v = default
            edit.setText(str(default))
        backend.set_config(key, v)

    def _save_log_max(self):
        self._save_int_config(self.log_max_edit, "log_max_files", 500)

    def _save_log_age(self):
        self._save_int_config(self.log_age_edit, "log_max_age_days", 30)

    def _save_log_size(self):
        self._save_int_config(self.log_size_edit, "log_max_size_mb", 500)

    def _save_result_size(self):
        self._save_int_config(self.result_size_edit, "result_max_size_mb", 5000)

    def _clear_history(self):
        box = MessageBox(tr("Clear History", self._lang),
                         tr("Clear history confirm", self._lang), self.window())
        if box.exec():
            from core.translation_history import TranslationHistoryManager
            _, _, log_dir = backend.get_custom_paths()
            TranslationHistoryManager(log_dir=log_dir).clear_all_records()

    def _clear_history_and_files(self):
        box = MessageBox(tr("Clear History And Files", self._lang),
                         tr("Clear history and files confirm", self._lang), self.window())
        if box.exec():
            from core.translation_history import TranslationHistoryManager
            _, _, log_dir = backend.get_custom_paths()
            info = TranslationHistoryManager(log_dir=log_dir).clear_all_records_and_files()
            from qfluentwidgets import InfoBar
            InfoBar.success(
                tr("Clear History And Files", self._lang),
                f"{info.get('files_deleted', 0)} files deleted",
                duration=3000, parent=self.window())

    def _pick_output_dir(self):
        current = self.output_edit.text() or os.getcwd()
        path = QFileDialog.getExistingDirectory(
            self, tr("Output Folder", self._lang), current)
        if path:
            self.output_edit.setText(path)
            backend.set_config("result_dir", path)

    # --- model management ---
    def _on_mode_changed(self, index):
        if 0 <= index < len(self._modes):
            backend.set_config("translation_mode", self._modes[index][0])

    # ----- Model management: per-model install / delete / use -----
    def _make_model_section(self, title, tip_key=None):
        """A collapsible section: a checkable header button that shows/hides a
        body widget into which model rows are filled. Returns (header, body, layout)."""
        header = PushButton(title)
        header.setCheckable(True)
        header.setChecked(True)
        if tip_key:
            header.setToolTip(tr(tip_key, self._lang))
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 4, 0, 8)
        bl.setSpacing(6)
        header.toggled.connect(body.setVisible)
        return header, body, bl

    def _toast(self, title, text, error=False):
        """Top-right transient notification (model install/delete result)."""
        from qfluentwidgets import InfoBar, InfoBarPosition
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self.window())

    def _tag_chip(self, key):
        from qfluentwidgets import isDarkTheme
        chip = QLabel(tr(key, self._lang))
        if key == "Tag Recommended":
            bg, fg = "rgba(10,132,255,0.20)", "#3aa0ff"
        elif isDarkTheme():
            bg, fg = "rgba(255,255,255,0.13)", "#dde3ee"
        else:
            bg, fg = "rgba(0,0,0,0.07)", "#5a6473"
        chip.setStyleSheet(
            f"QLabel{{font-size:10px;font-weight:600;padding:1px 7px;"
            f"border-radius:8px;background:{bg};color:{fg};}}")
        return chip

    def _refresh_models(self):
        self.models_dir_edit.setText(model_store.current_dir())
        self._fill_model_rows(self._ocr_body_layout, "Image OCR")
        self._fill_model_rows(self._stt_body_layout, "Video/Audio")

    def _fill_model_rows(self, layout, plugin):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        from core.optional_modules import plugin_model_states
        for st in plugin_model_states(plugin):
            layout.addWidget(self._model_row_widget(plugin, st))

    def _model_row_widget(self, plugin, st):
        # Management = download state only: not-downloaded -> Install, downloaded
        # -> Delete. The model actually used is chosen at translate time (the
        # media STT picker), so there's no "active" concept here.
        row = QWidget()
        row.setObjectName("modelRow")   # scope the border to the row, NOT children
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(8)
        h.addWidget(StrongBodyLabel(st["label"]))
        for t in st.get("tags", []):
            h.addWidget(self._tag_chip(t))
        h.addStretch(1)
        size = st.get("size", "")
        if st.get("vram"):
            size = f"{size} · {st['vram']}"
        h.addWidget(CaptionLabel(size))
        if not st["downloaded"]:
            b = PushButton(tr("Install", self._lang))
            b.clicked.connect(lambda _=False, p=plugin, i=st["id"], btn=b: self._install_model(p, i, btn))
            h.addWidget(b)
        else:
            d = PushButton(tr("Delete", self._lang))
            d.clicked.connect(lambda _=False, p=plugin, i=st["id"], lbl=st["label"], btn=d: self._delete_model(p, i, lbl, btn))
            h.addWidget(d)
        row.setStyleSheet(
            "#modelRow{border:1px solid rgba(128,128,128,0.28);border-radius:8px;}")
        return row

    def _install_model(self, plugin, model_id, btn):
        from qt_app.worker import ModelDownloadWorker
        btn.setEnabled(False)
        btn.setText(tr("Downloading", self._lang))
        w = ModelDownloadWorker(plugin, model_id)
        self._model_workers.append(w)
        label = next((s["label"] for s in self._all_states(plugin) if s["id"] == model_id), model_id)

        def done(ok):
            self._toast(tr("Model Management", self._lang),
                        (tr("Model Installed", self._lang) + "：" + label) if ok
                        else tr("Install failed", self._lang), error=not ok)
            if w in self._model_workers:
                self._model_workers.remove(w)
            self._refresh_models()
        w.finished_ok.connect(done)
        w.start()

    def _delete_model(self, plugin, model_id, label, btn):
        box = MessageBox(tr("Model Management", self._lang),
                         tr("Delete Model Confirm", self._lang), self.window())
        if not box.exec():
            return
        from qt_app.worker import ModelDeleteWorker
        btn.setEnabled(False)
        btn.setText(tr("Deleting", self._lang))
        w = ModelDeleteWorker(plugin, model_id)
        self._model_workers.append(w)

        def done(ok):
            self._toast(tr("Model Management", self._lang),
                        (tr("Model Deleted", self._lang) + "：" + label) if ok
                        else tr("Delete failed", self._lang), error=not ok)
            if w in self._model_workers:
                self._model_workers.remove(w)
            self._refresh_models()
        w.finished_ok.connect(done)
        w.start()

    def _all_states(self, plugin):
        from core.optional_modules import plugin_model_states
        return plugin_model_states(plugin)

    def _change_models_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, tr("Model Location", self._lang), model_store.current_dir())
        if not path or os.path.abspath(path) == model_store.current_dir():
            return
        box = MessageBox(tr("Model Management", self._lang),
                         tr("Move existing models to the new location?", self._lang), self)
        move = box.exec()
        ok, msg = model_store.set_models_dir(path, move=move)
        if ok:
            model_store.setup_model_env()
            self._refresh_models()
            self._info(tr("Model Management", self._lang),
                       tr("Restart to apply", self._lang))
        else:
            self._info(tr("Model Management", self._lang), msg, error=True)

    def _info(self, title, text, error=False):
        from qfluentwidgets import InfoBar, InfoBarPosition
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _apply_tips(self):
        """Hover tooltips explaining each non-obvious option. To make them
        DISCOVERABLE and responsive (the native tooltip is slow and gives no
        cue): each labelled option gets a help-cursor on hover, a soft highlight,
        and a fast styled tooltip (ToolTipFilter, ~250ms). Tip text lives in the
        locale files keyed '<Label> Tip'."""
        from PySide6.QtCore import Qt
        self._tip_filters = getattr(self, "_tip_filters", [])
        pairs = [
            (self.mode_label, getattr(self, "mode_combo", None), "Translation Mode Tip"),
            (self.auto_glossary_label, getattr(self, "auto_glossary", None), "AI Glossary Extraction Tip"),
            (self.mask_ph_label, getattr(self, "mask_ph", None), "Placeholder Protection Tip"),
            (self.dedup_ctx_label, getattr(self, "dedup_ctx", None), "Context-aware Dedup Tip"),
            (self.with_ctx_label, getattr(self, "with_ctx", None), "Type Context Tip"),
            (self.tone_label, getattr(self, "tone_combo", None), "Tone Tip"),
            (self.length_label, getattr(self, "length_combo", None), "Length Tip"),
            (self.style_label, getattr(self, "style_edit", None), "Style Guide Tip"),
            (self.bi_bold_label, getattr(self, "bi_bold", None), "Bilingual Bold Tip"),
            (self.bi_color_label, getattr(self, "bi_color", None), "Translation Color Tip"),
            (self.live_stream_label, getattr(self, "live_stream", None), "Stream Translation Tip"),
            (self.hang_label, getattr(self, "hang_combo", None), "Segmentation Pause Tip"),
            (self.sens_label, getattr(self, "sens_combo", None), "Mic Sensitivity Tip"),
            (self.maxseg_label, getattr(self, "maxseg_combo", None), "Force Cut Tip"),
            (self.hist_max_label, getattr(self, "hist_max", None), "Auto-delete by count Tip"),
            (self.hist_age_label, getattr(self, "hist_age", None), "Auto-delete by age Tip"),
            (self.ocr_header, None, "Image OCR Model Tip"),
            (self.stt_header, None, "Speech-to-Text Model Tip"),
            (self.models_loc_label, getattr(self, "models_dir_edit", None), "Model Location Tip"),
            (getattr(self, "lan_label", None), getattr(self, "lan_switch", None), "LAN Mode Tip"),
            (getattr(self, "lan_admin_label", None), getattr(self, "lan_admin_edit", None), "LAN admin password Tip"),
        ]
        _hover_qss = ("QLabel:hover{background:rgba(0,120,212,0.13);"
                      "border-radius:4px;}")
        for label, control, key in pairs:
            tip = tr(key, self._lang)
            if label is not None:
                label.setToolTip(tip)
                label.setCursor(Qt.WhatsThisCursor)       # ?-cursor: "there's help here"
                label.setStyleSheet(_hover_qss)           # soft highlight on hover
                if not getattr(label, "_has_tip_filter", False):
                    self._tip_filters.append(
                        ToolTipFilter(label, showDelay=250, position=ToolTipPosition.TOP))
                    label.installEventFilter(self._tip_filters[-1])
                    label._has_tip_filter = True
            if control is not None:
                control.setToolTip(tip)
                if not getattr(control, "_has_tip_filter", False):
                    self._tip_filters.append(
                        ToolTipFilter(control, showDelay=250, position=ToolTipPosition.TOP))
                    control.installEventFilter(self._tip_filters[-1])
                    control._has_tip_filter = True

    def retranslate(self, lang):
        self._lang = lang
        self.section_translation.setText(tr("Settings", lang))
        self.per_model_hint.setText(tr("Per Model Hint", lang))
        self.card_run.set_title(tr("Run Mode", lang))
        self.lan_label.setText(tr("LAN Mode", lang))
        self.lan_hint.setText(tr("LAN access hint", lang))
        self.lan_admin_label.setText(tr("LAN admin password", lang))
        self.card_options.set_title(tr("Translation Options", lang))
        self.card_advanced.set_title(tr("Advanced Options", lang))
        self.card_live.set_title(tr("Real-Time Voice", lang))
        self.mode_label.setText(tr("Translation Mode", lang))
        self.auto_glossary_label.setText(tr("AI Glossary Extraction", lang))
        self.mask_ph_label.setText(tr("Placeholder Protection", lang))
        self.dedup_ctx_label.setText(tr("Context-aware Dedup", lang))
        self.with_ctx_label.setText(tr("Type Context", lang))
        self.tone_label.setText(tr("Tone", lang))
        self.length_label.setText(tr("Length", lang))
        self.style_label.setText(tr("Style Guide", lang))
        self.bi_bold_label.setText(tr("Bilingual Bold", lang))
        self.bi_color_label.setText(tr("Translation Color", lang))
        self.live_stream_label.setText(tr("Stream Translation", lang))
        self.card_data.set_title(tr("Data & Storage", lang))
        self.output_label.setText(tr("Output Folder", lang))
        self.output_browse.setText(tr("Browse", lang))
        self.hist_max_label.setText(tr("Result Max Count", lang))
        self.result_size_label.setText(tr("Result Max Size", lang))
        self.hist_age_label.setText(tr("Result Max Days", lang))
        self.log_max_label.setText(tr("Log Max Files", lang))
        self.log_size_label.setText(tr("Log Max Size", lang))
        self.log_age_label.setText(tr("Log Max Days", lang))
        self.hist_clear_btn.setText(tr("Clear History", lang))
        self.hist_clear_files_btn.setText(tr("Clear History And Files", lang))
        self.card_models.set_title(tr("Model Management", lang))
        self.models_loc_label.setText(tr("Model Location", lang))
        self.models_browse.setText(tr("Change Location", lang))
        self.ocr_header.setText(tr("Image OCR Model", lang))
        self.stt_header.setText(tr("Speech-to-Text Model", lang))
        self._refresh_models()   # rebuild rows in the new language
        self.stt_scope_hint.setText(tr("STT Scope Hint", lang))
        self.stt_hint.setText(tr("Whisper Hint", lang))
        self._apply_tips()
