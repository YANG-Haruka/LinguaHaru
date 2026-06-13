"""Settings page: every change is persisted to system_config.json immediately."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, SpinBox,
    CardWidget, CaptionLabel, IndicatorPosition, ComboBox,
)

from qt_app import backend
from qt_app.i18n import tr, UI_LANGS, lang_display_name, lang_from_display_name
from config.optional_modules import module_status


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

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(16)

        config = backend.read_config()

        self.section_translation = StrongBodyLabel(tr("Settings", lang))
        layout.addWidget(self.section_translation)

        # --- UI language selector ---
        lang_card = CardWidget()
        lang_form = QFormLayout(lang_card)
        lang_form.setContentsMargins(20, 16, 20, 16)
        lang_form.setSpacing(12)
        self.ui_lang_combo = ComboBox()
        self.ui_lang_combo.addItems([lang_display_name(l) for l in UI_LANGS])
        self.ui_lang_combo.setCurrentText(lang_display_name(lang))
        self.ui_lang_combo.currentTextChanged.connect(self._on_lang_combo)
        # No dedicated label key exists; tr() falls back to the key text.
        self.ui_lang_label = BodyLabel(tr("Interface Language", lang))
        lang_form.addRow(self.ui_lang_label, self.ui_lang_combo)
        layout.addWidget(lang_card)

        general = CardWidget()
        form = QFormLayout(general)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)

        self.online_switch = SwitchButton()
        self.online_switch.setChecked(config.get("default_online", False))
        self.online_switch.checkedChanged.connect(
            lambda v: backend.set_config("default_online", v))
        self.online_label = BodyLabel(tr("Use Online Model", lang))
        form.addRow(self.online_label, self.online_switch)

        self.lan_switch = SwitchButton()
        self.lan_switch.setChecked(config.get("lan_mode", False))
        self.lan_switch.checkedChanged.connect(
            lambda v: backend.set_config("lan_mode", v))
        self.lan_label = BodyLabel(tr("Local Network Mode (Restart to Apply)", lang))
        form.addRow(self.lan_label, self.lan_switch)

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

        self.auto_glossary = SwitchButton()
        self.auto_glossary.setChecked(config.get("auto_extract_glossary", False))
        self.auto_glossary.checkedChanged.connect(
            lambda v: backend.set_config("auto_extract_glossary", v))
        self.auto_glossary_label = BodyLabel(tr("AI Glossary Extraction", lang))
        form.addRow(self.auto_glossary_label, self.auto_glossary)

        layout.addWidget(general)

        # --- Optional Modules status ---
        self.section_modules = StrongBodyLabel(tr("Optional Modules", lang))
        layout.addWidget(self.section_modules)
        modules_card = CardWidget()
        mod_layout = QVBoxLayout(modules_card)
        mod_layout.setContentsMargins(20, 16, 20, 16)
        mod_layout.setSpacing(10)
        for mod in module_status():
            row = QHBoxLayout()
            mark = "[OK]" if mod["available"] else "[--]"
            name = BodyLabel(f"{mark} {mod['name']} ({mod['detail']})")
            row.addWidget(name)
            row.addStretch(1)
            if not mod["available"]:
                row.addWidget(CaptionLabel(mod["install"]))
            mod_layout.addLayout(row)
        layout.addWidget(modules_card)

        layout.addStretch(1)

    def _on_lang_combo(self, display):
        lang = lang_from_display_name(display)
        if callable(self.on_ui_lang_changed):
            self.on_ui_lang_changed(lang)

    def retranslate(self, lang):
        self._lang = lang
        # keep the selector in sync without re-triggering the callback
        self.ui_lang_combo.blockSignals(True)
        self.ui_lang_combo.setCurrentText(lang_display_name(lang))
        self.ui_lang_combo.blockSignals(False)
        self.ui_lang_label.setText(tr("Interface Language", lang))
        self.section_translation.setText(tr("Settings", lang))
        self.online_label.setText(tr("Use Online Model", lang))
        self.lan_label.setText(tr("Local Network Mode (Restart to Apply)", lang))
        self.thread_online_label.setText(tr("Thread Count", lang) + " (online)")
        self.thread_offline_label.setText(tr("Thread Count", lang) + " (offline)")
        self.max_retries_label.setText(tr("Max Retries", lang))
        self.rpm_label.setText(tr("RPM Limit (0 = unlimited, restart to apply)", lang))
        self.auto_glossary_label.setText(tr("AI Glossary Extraction", lang))
        self.section_modules.setText(tr("Optional Modules", lang))
