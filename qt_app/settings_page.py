"""Settings page: every change is persisted to system_config.json immediately."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QFileDialog

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, SpinBox,
    CardWidget, PushButton, LineEdit, PasswordLineEdit, FluentIcon,
    InfoBar, InfoBarPosition,
)

from core import backend
from qt_app.i18n import tr
from core.api_keys import load_api_key_for_model, save_api_key_for_model

_GOOGLE_PROVIDER = "(Google) Live Translate"


class SettingsPage(ScrollArea):
    """Online-by-default, LAN mode, thread counts, retries, RPM limit, AI
    glossary extraction, and an Optional Modules status group."""

    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self._lang = lang
        # Set by MainWindow so the language selector can drive a global retranslate.
        self.on_ui_lang_changed = None
        self.setWidgetResizable(True)
        # Without this, the scroll viewport paints the system palette (dark on a
        # Windows dark desktop) even in light mode -> "light nav, dark settings".
        self.enableTransparentBackground()

        container = QWidget()
        container.setObjectName("settingsScrollContainer")
        container.setStyleSheet(
            "#settingsScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(16)

        config = backend.read_config()

        self.section_translation = StrongBodyLabel(tr("Settings", lang))
        layout.addWidget(self.section_translation)

        # (Interface language now lives at the bottom of the nav rail.)

        # Model-related parameters (thread counts, retries, RPM, LAN mode).
        general = CardWidget()
        form = QFormLayout(general)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)

        # (LAN mode is a Web-only concept — the desktop app runs local-only.)
        self.thread_online = SpinBox()
        self.thread_online.setRange(1, 64)
        self.thread_online.setValue(config.get("default_thread_count_online", 2))
        self.thread_online.valueChanged.connect(
            lambda v: backend.set_config("default_thread_count_online", v))
        self.thread_online_label = BodyLabel(tr("Thread Count", lang) + " (online)")
        form.addRow(self.thread_online_label, self.thread_online)

        self.thread_offline = SpinBox()
        self.thread_offline.setRange(1, 64)
        self.thread_offline.setValue(config.get("default_thread_count_offline", 4))
        self.thread_offline.valueChanged.connect(
            lambda v: backend.set_config("default_thread_count_offline", v))
        self.thread_offline_label = BodyLabel(tr("Thread Count", lang) + " (offline)")
        form.addRow(self.thread_offline_label, self.thread_offline)

        self.max_retries = SpinBox()
        self.max_retries.setRange(0, 50)
        self.max_retries.setValue(config.get("max_retries", 4))
        self.max_retries.valueChanged.connect(
            lambda v: backend.set_config("max_retries", v))
        self.max_retries_label = BodyLabel(tr("Max Retries", lang))
        form.addRow(self.max_retries_label, self.max_retries)

        self.rpm_limit = SpinBox()
        self.rpm_limit.setRange(0, 100000)
        self.rpm_limit.setValue(config.get("rpm_limit", 0))
        self.rpm_limit.valueChanged.connect(
            lambda v: backend.set_config("rpm_limit", v))
        self.rpm_label = BodyLabel(tr("RPM Limit (0 = unlimited, restart to apply)", lang))
        form.addRow(self.rpm_label, self.rpm_limit)

        layout.addWidget(general)

        # --- Translation options (not model-related): AI glossary + output dir ---
        # AI glossary extraction — its own card.
        self.section_options = StrongBodyLabel(tr("Translation Options", lang))
        layout.addWidget(self.section_options)
        glossary_card = CardWidget()
        gl_form = QFormLayout(glossary_card)
        gl_form.setContentsMargins(20, 16, 20, 16)
        gl_form.setSpacing(12)
        self.auto_glossary = SwitchButton()
        self.auto_glossary.setChecked(config.get("auto_extract_glossary", False))
        self.auto_glossary.checkedChanged.connect(
            lambda v: backend.set_config("auto_extract_glossary", v))
        self.auto_glossary_label = BodyLabel(tr("AI Glossary Extraction", lang))
        gl_form.addRow(self.auto_glossary_label, self.auto_glossary)
        layout.addWidget(glossary_card)

        # Output folder — separate card/section.
        self.section_output = StrongBodyLabel(tr("Output Folder", lang))
        layout.addWidget(self.section_output)
        output_card = CardWidget()
        out_form = QFormLayout(output_card)
        out_form.setContentsMargins(20, 16, 20, 16)
        out_form.setSpacing(12)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.output_edit = LineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(config.get("result_dir", "result"))
        self.output_browse = PushButton(FluentIcon.FOLDER, tr("Browse", lang))
        self.output_browse.clicked.connect(self._pick_output_dir)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.output_browse)
        self.output_label = BodyLabel(tr("Output Folder", lang))
        out_form.addRow(self.output_label, out_row)
        layout.addWidget(output_card)

        # --- Google API key (real-time voice translation) ---
        self.section_google = StrongBodyLabel(tr("Google API Key", lang))
        layout.addWidget(self.section_google)
        google_card = CardWidget()
        g_form = QFormLayout(google_card)
        g_form.setContentsMargins(20, 16, 20, 16)
        g_form.setSpacing(12)
        self.google_key_edit = PasswordLineEdit()
        self.google_key_edit.setText(load_api_key_for_model(_GOOGLE_PROVIDER))
        self.google_key_edit.setPlaceholderText("AIza... / AQ...")
        self.google_key_edit.editingFinished.connect(self._save_google_key)
        self.google_key_label = BodyLabel(tr("Google API Key", lang))
        g_form.addRow(self.google_key_label, self.google_key_edit)
        self.google_hint = BodyLabel(tr("Google API Key Hint", lang))
        g_form.addRow("", self.google_hint)
        layout.addWidget(google_card)

        layout.addStretch(1)

    def _save_google_key(self):
        save_api_key_for_model(_GOOGLE_PROVIDER, self.google_key_edit.text().strip())
        InfoBar.success(
            tr("Settings", self._lang), tr("Google key saved", self._lang),
            orient=1, isClosable=True, position=InfoBarPosition.TOP,
            duration=3000, parent=self)

    def _pick_output_dir(self):
        current = self.output_edit.text() or os.getcwd()
        path = QFileDialog.getExistingDirectory(
            self, tr("Output Folder", self._lang), current)
        if path:
            self.output_edit.setText(path)
            backend.set_config("result_dir", path)

    def retranslate(self, lang):
        self._lang = lang
        self.section_translation.setText(tr("Settings", lang))
        self.thread_online_label.setText(tr("Thread Count", lang) + " (online)")
        self.thread_offline_label.setText(tr("Thread Count", lang) + " (offline)")
        self.max_retries_label.setText(tr("Max Retries", lang))
        self.rpm_label.setText(tr("RPM Limit (0 = unlimited, restart to apply)", lang))
        self.section_options.setText(tr("Translation Options", lang))
        self.auto_glossary_label.setText(tr("AI Glossary Extraction", lang))
        self.section_output.setText(tr("Output Folder", lang))
        self.output_label.setText(tr("Output Folder", lang))
        self.output_browse.setText(tr("Browse", lang))
        self.section_google.setText(tr("Google API Key", lang))
        self.google_key_label.setText(tr("Google API Key", lang))
        self.google_hint.setText(tr("Google API Key Hint", lang))
