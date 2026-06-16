"""Settings page: every change is persisted to system_config.json immediately.

Structure mirrors the Web settings exactly: four collapsible cards —
Run Mode (LAN) · Translation Options · Data & Storage · Model Management.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QFileDialog,
    QLineEdit, QApplication,
)

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, CaptionLabel,
    CardWidget, PushButton, LineEdit, FluentIcon, MessageBox, ComboBox,
)

from core import backend
from core import model_store
from qt_app.i18n import tr


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
        self.card_options = _CollapsibleCard(tr("Translation Options", lang))
        layout.addWidget(self.card_options)
        gl_form = QFormLayout()
        gl_form.setSpacing(12)
        # Translation mode (precise / natural / polish / subtitle)
        self._modes = []
        self.mode_combo = ComboBox()
        try:
            from core.translation_modes import load_modes, get_active_mode
            self._modes = list(load_modes().items())
            cur = get_active_mode()
            for i, (mid, m) in enumerate(self._modes):
                self.mode_combo.addItem(m.get("label", mid))
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
        gl_form.addRow(self.auto_glossary_label, self.auto_glossary)
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
        gl_form.addRow(self.dedup_ctx_label, self.dedup_ctx)
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
        gl_form.addRow(self.live_stream_label, self.live_stream)
        self.card_options.body.addLayout(gl_form)

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
        hist_form = QFormLayout()
        hist_form.setSpacing(12)
        self.hist_max_edit = LineEdit()
        self.hist_max_edit.setText(str(config.get("history_max_records", 1000)))
        self.hist_max_edit.editingFinished.connect(self._save_hist_max)
        self.hist_max_label = BodyLabel(tr("Auto-delete by count", lang))
        hist_form.addRow(self.hist_max_label, self.hist_max_edit)
        self.hist_age_edit = LineEdit()
        self.hist_age_edit.setText(str(config.get("history_max_age_days", 0)))
        self.hist_age_edit.editingFinished.connect(self._save_hist_age)
        self.hist_age_label = BodyLabel(tr("Auto-delete by age", lang))
        hist_form.addRow(self.hist_age_label, self.hist_age_edit)
        self.card_data.body.addLayout(hist_form)
        self.hist_clear_btn = PushButton(FluentIcon.DELETE, tr("Clear History", lang))
        self.hist_clear_btn.clicked.connect(self._clear_history)
        self.card_data.body.addWidget(self.hist_clear_btn)
        self.hist_clear_files_btn = PushButton(FluentIcon.DELETE, tr("Clear History And Files", lang))
        self.hist_clear_files_btn.clicked.connect(self._clear_history_and_files)
        self.card_data.body.addWidget(self.hist_clear_files_btn)

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
        self.models_list_host = QVBoxLayout()
        self.models_list_host.setSpacing(4)
        self.card_models.body.addLayout(self.models_list_host)
        # Image-OCR model picker.
        from core.optional_modules import ocr_models, get_selected_ocr_model
        self._ocr_models = ocr_models()
        ocr_row = QHBoxLayout()
        ocr_row.setSpacing(8)
        self.ocr_mm_label = BodyLabel(tr("Image OCR Model", self._lang))
        ocr_row.addWidget(self.ocr_mm_label)
        self.ocr_mm_combo = ComboBox()
        for m in self._ocr_models:
            self.ocr_mm_combo.addItem(m["label"])
        cur_ocr = get_selected_ocr_model()
        for i, m in enumerate(self._ocr_models):
            if m["id"] == cur_ocr:
                self.ocr_mm_combo.setCurrentIndex(i)
                break
        self.ocr_mm_combo.currentIndexChanged.connect(self._on_ocr_changed)
        ocr_row.addWidget(self.ocr_mm_combo, 1)
        self.card_models.body.addLayout(ocr_row)
        from core.pipelines.video_translation_pipeline import (
            STT_MODELS, get_selected_stt_model)
        self._stt_models = STT_MODELS
        stt_row = QHBoxLayout()
        stt_row.setSpacing(8)
        self.stt_mm_label = BodyLabel(tr("Speech-to-Text Model", self._lang))
        stt_row.addWidget(self.stt_mm_label)
        self.stt_mm_combo = ComboBox()
        for m in STT_MODELS:
            self.stt_mm_combo.addItem(m["label"])
        cur = get_selected_stt_model()
        for i, m in enumerate(STT_MODELS):
            if m["id"] == cur:
                self.stt_mm_combo.setCurrentIndex(i)
                break
        self.stt_mm_combo.currentIndexChanged.connect(self._on_stt_changed)
        stt_row.addWidget(self.stt_mm_combo, 1)
        self.card_models.body.addLayout(stt_row)
        self.stt_scope_hint = CaptionLabel(tr("STT Scope Hint", self._lang))
        self.stt_scope_hint.setWordWrap(True)
        self.card_models.body.addWidget(self.stt_scope_hint)
        self.stt_hint = CaptionLabel(tr("Whisper Hint", self._lang))
        self.stt_hint.setWordWrap(True)
        self.card_models.body.addWidget(self.stt_hint)
        self._refresh_models()

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
    def _on_stt_changed(self, index):
        if 0 <= index < len(self._stt_models):
            backend.set_config("stt_model", self._stt_models[index]["id"])
            # Free the previously-loaded STT model if no feature uses it anymore.
            try:
                from core.pipelines.video_translation_pipeline import release_unused_stt_models
                release_unused_stt_models()
            except Exception:  # noqa: BLE001
                pass

    def _on_mode_changed(self, index):
        if 0 <= index < len(self._modes):
            backend.set_config("translation_mode", self._modes[index][0])

    def _on_ocr_changed(self, index):
        if 0 <= index < len(self._ocr_models):
            backend.set_config("ocr_model_size", self._ocr_models[index]["id"])

    def _refresh_models(self):
        self.models_dir_edit.setText(model_store.current_dir())
        while self.models_list_host.count():
            item = self.models_list_host.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        models = model_store.list_models()
        if not models:
            self.models_list_host.addWidget(CaptionLabel(tr("No models downloaded", self._lang)))
            return
        for m in models:
            self.models_list_host.addWidget(CaptionLabel(f"• {m['label']} — {m['size_h']}"))

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

    def retranslate(self, lang):
        self._lang = lang
        self.section_translation.setText(tr("Settings", lang))
        self.per_model_hint.setText(tr("Per Model Hint", lang))
        self.card_run.set_title(tr("Run Mode", lang))
        self.lan_label.setText(tr("LAN Mode", lang))
        self.lan_hint.setText(tr("LAN access hint", lang))
        self.lan_admin_label.setText(tr("LAN admin password", lang))
        self.card_options.set_title(tr("Translation Options", lang))
        self.mode_label.setText(tr("Translation Mode", lang))
        self.auto_glossary_label.setText(tr("AI Glossary Extraction", lang))
        self.mask_ph_label.setText(tr("Placeholder Protection", lang))
        self.dedup_ctx_label.setText(tr("Context-aware Dedup", lang))
        self.bi_bold_label.setText(tr("Bilingual Bold", lang))
        self.bi_color_label.setText(tr("Translation Color", lang))
        self.live_stream_label.setText(tr("Stream Translation", lang))
        self.card_data.set_title(tr("Data & Storage", lang))
        self.output_label.setText(tr("Output Folder", lang))
        self.output_browse.setText(tr("Browse", lang))
        self.hist_max_label.setText(tr("Auto-delete by count", lang))
        self.hist_age_label.setText(tr("Auto-delete by age", lang))
        self.hist_clear_btn.setText(tr("Clear History", lang))
        self.hist_clear_files_btn.setText(tr("Clear History And Files", lang))
        self.card_models.set_title(tr("Model Management", lang))
        self.models_loc_label.setText(tr("Model Location", lang))
        self.models_browse.setText(tr("Change Location", lang))
        self.ocr_mm_label.setText(tr("Image OCR Model", lang))
        self.stt_mm_label.setText(tr("Speech-to-Text Model", lang))
        self.stt_scope_hint.setText(tr("STT Scope Hint", lang))
        self.stt_hint.setText(tr("Whisper Hint", lang))
