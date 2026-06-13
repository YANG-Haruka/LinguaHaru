"""Settings page: every change is persisted to system_config.json immediately."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, SpinBox,
    CardWidget, CaptionLabel, IndicatorPosition,
)

from qt_app import backend
from config.optional_modules import module_status


class SettingsPage(ScrollArea):
    """Online-by-default, LAN mode, thread counts, retries, RPM limit, AI
    glossary extraction, and an Optional Modules status group."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self.setWidgetResizable(True)

        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(16)

        config = backend.read_config()

        layout.addWidget(StrongBodyLabel("Translation"))

        general = CardWidget()
        form = QFormLayout(general)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(12)

        self.online_switch = SwitchButton()
        self.online_switch.setChecked(config.get("default_online", False))
        self.online_switch.checkedChanged.connect(
            lambda v: backend.set_config("default_online", v))
        form.addRow(BodyLabel("Online by default"), self.online_switch)

        self.lan_switch = SwitchButton()
        self.lan_switch.setChecked(config.get("lan_mode", False))
        self.lan_switch.checkedChanged.connect(
            lambda v: backend.set_config("lan_mode", v))
        form.addRow(BodyLabel("LAN mode (restart to apply)"), self.lan_switch)

        self.thread_online = SpinBox()
        self.thread_online.setRange(1, 64)
        self.thread_online.setValue(config.get("default_thread_count_online", 2))
        self.thread_online.valueChanged.connect(
            lambda v: backend.set_config("default_thread_count_online", v))
        form.addRow(BodyLabel("Thread count (online)"), self.thread_online)

        self.thread_offline = SpinBox()
        self.thread_offline.setRange(1, 64)
        self.thread_offline.setValue(config.get("default_thread_count_offline", 4))
        self.thread_offline.valueChanged.connect(
            lambda v: backend.set_config("default_thread_count_offline", v))
        form.addRow(BodyLabel("Thread count (offline)"), self.thread_offline)

        self.max_retries = SpinBox()
        self.max_retries.setRange(0, 50)
        self.max_retries.setValue(config.get("max_retries", 4))
        self.max_retries.valueChanged.connect(
            lambda v: backend.set_config("max_retries", v))
        form.addRow(BodyLabel("Max retries"), self.max_retries)

        self.rpm_limit = SpinBox()
        self.rpm_limit.setRange(0, 100000)
        self.rpm_limit.setValue(config.get("rpm_limit", 0))
        self.rpm_limit.valueChanged.connect(
            lambda v: backend.set_config("rpm_limit", v))
        form.addRow(BodyLabel("RPM limit (0 = unlimited, restart to apply)"), self.rpm_limit)

        self.auto_glossary = SwitchButton()
        self.auto_glossary.setChecked(config.get("auto_extract_glossary", False))
        self.auto_glossary.checkedChanged.connect(
            lambda v: backend.set_config("auto_extract_glossary", v))
        form.addRow(BodyLabel("AI glossary extraction"), self.auto_glossary)

        layout.addWidget(general)

        # --- Optional Modules status ---
        layout.addWidget(StrongBodyLabel("Optional Modules"))
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
