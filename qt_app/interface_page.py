"""Interface Management page: pick the active translation interface.

Three grouped sections of provider entry cards:
  - Local: models from backend.scan_local_models() (Ollama / LM Studio)
  - Official: bundled config/api_config/*.json presets
  - Custom: user-added configs

Clicking an entry sets it active (persisted via backend.set_active_model so the
Translate page picks it up). "Add Interface" opens a MessageBoxBase dialog that
writes a new config/api_config/<name>.json.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel,
    StrongBodyLabel, PrimaryPushButton, PillPushButton, FluentIcon, FlowLayout,
    SimpleCardWidget, IconWidget, InfoBar, InfoBarPosition, MessageBoxBase,
    LineEdit, RoundMenu, Action,
)

from qt_app import backend
from qt_app.i18n import tr
from qt_app.widgets import EntryCard
from config.api_keys import load_api_key_for_model, save_api_key_for_model


class AddInterfaceDialog(MessageBoxBase):
    """Create/edit an api_config/<name>.json (base_url, model, key, temp, top_p)."""

    def __init__(self, parent=None, lang="en", existing=None):
        super().__init__(parent)
        self._lang = lang
        self.titleLabel = SubtitleLabel(tr("Add Interface", lang), self)
        self.viewLayout.addWidget(self.titleLabel)

        form_host = QWidget(self)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 8, 0, 0)
        form.setSpacing(10)

        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("My Provider")
        self.base_edit = LineEdit(self)
        self.base_edit.setPlaceholderText("https://api.example.com/v1")
        self.model_edit = LineEdit(self)
        self.model_edit.setPlaceholderText("gpt-4o")
        self.key_edit = LineEdit(self)
        self.key_edit.setPlaceholderText("sk-...")
        self.temp_edit = LineEdit(self)
        self.temp_edit.setPlaceholderText("0.7")
        self.topp_edit = LineEdit(self)
        self.topp_edit.setPlaceholderText("0.95")

        form.addRow(tr("Interface Name", lang), self.name_edit)
        form.addRow(tr("Base URL", lang), self.base_edit)
        form.addRow(tr("Model ID", lang), self.model_edit)
        form.addRow(tr("API Key", lang), self.key_edit)
        form.addRow(tr("Temperature", lang), self.temp_edit)
        form.addRow(tr("Top P", lang), self.topp_edit)
        self.viewLayout.addWidget(form_host)

        self.yesButton.setText(tr("Save", lang))
        self.cancelButton.setText(tr("Cancel", lang))
        self.widget.setMinimumWidth(420)

        if existing:
            cfg = backend.read_api_config(existing) or {}
            self.name_edit.setText(existing)
            self.name_edit.setEnabled(False)
            self.base_edit.setText(str(cfg.get("base_url", "")))
            self.model_edit.setText(str(cfg.get("model", "")))
            # Key is stored per provider in mykeys/, not in the config JSON.
            self.key_edit.setText(load_api_key_for_model(existing))
            self.temp_edit.setText(str(cfg.get("temperature", "")))
            self.topp_edit.setText(str(cfg.get("top_p", "")))

    def api_key(self):
        return self.key_edit.text().strip()

    def values(self):
        def _num(text):
            try:
                return float(text)
            except (TypeError, ValueError):
                return None
        # The API key is intentionally NOT written into the config JSON; it is
        # saved separately per provider in mykeys/ (see on_add).
        return self.name_edit.text().strip(), {
            "base_url": self.base_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "temperature": _num(self.temp_edit.text().strip()),
            "top_p": _num(self.topp_edit.text().strip()),
        }


class _GroupCard(SimpleCardWidget):
    """A titled section card (icon + title + subtitle) holding a FlowLayout of
    entry cards."""

    def __init__(self, title, subtitle, icon, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(26, 26)
        head.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = StrongBodyLabel(title, self)
        col.addWidget(self.title)
        self.subtitle = CaptionLabel(subtitle, self)
        col.addWidget(self.subtitle)
        head.addLayout(col)
        head.addStretch(1)
        layout.addLayout(head)

        self.flow_host = QWidget(self)
        self.flow = FlowLayout(self.flow_host, needAni=False)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setHorizontalSpacing(12)
        self.flow.setVerticalSpacing(12)
        layout.addWidget(self.flow_host)

    def clear(self):
        # takeAllWidgets() removes every widget from the layout and deletes them.
        self.flow.takeAllWidgets()

    def add_entry(self, widget):
        self.flow.addWidget(widget)


class InterfacePage(ScrollArea):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("InterfacePage")
        self._lang = lang
        self.setWidgetResizable(True)
        self.on_active_changed = None  # set by MainWindow to notify Translate page

        self.enableTransparentBackground()
        container = QWidget()
        container.setObjectName("interfaceScrollContainer")
        container.setStyleSheet(
            "#interfaceScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(16)

        # --- Header row: title + active pill + add button ---
        header = QHBoxLayout()
        header.setSpacing(12)
        self.title = TitleLabel(tr("Interface Management", lang))
        header.addWidget(self.title)
        self.active_pill = PillPushButton(FluentIcon.ACCEPT, "")
        self.active_pill.setCheckable(False)
        header.addWidget(self.active_pill)
        header.addStretch(1)
        self.add_btn = PrimaryPushButton(FluentIcon.ADD, tr("Add Interface", lang))
        self.add_btn.clicked.connect(self.on_add)
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        # --- Three group cards ---
        self.local_group = _GroupCard(
            tr("Local Interfaces", lang), tr("Local Interfaces Subtitle", lang),
            FluentIcon.IOT)
        self.official_group = _GroupCard(
            tr("Official Interfaces", lang), tr("Official Interfaces Subtitle", lang),
            FluentIcon.CLOUD)
        self.custom_group = _GroupCard(
            tr("Custom Interfaces", lang), tr("Custom Interfaces Subtitle", lang),
            FluentIcon.DEVELOPER_TOOLS)
        layout.addWidget(self.local_group)
        layout.addWidget(self.official_group)
        layout.addWidget(self.custom_group)
        layout.addStretch(1)

        self.reload()

    # --- i18n ---
    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Interface Management", lang))
        self.add_btn.setText(tr("Add Interface", lang))
        self.local_group.title.setText(tr("Local Interfaces", lang))
        self.local_group.subtitle.setText(tr("Local Interfaces Subtitle", lang))
        self.official_group.title.setText(tr("Official Interfaces", lang))
        self.official_group.subtitle.setText(tr("Official Interfaces Subtitle", lang))
        self.custom_group.title.setText(tr("Custom Interfaces", lang))
        self.custom_group.subtitle.setText(tr("Custom Interfaces Subtitle", lang))
        self.reload()

    # --- data ---
    def _active_name(self):
        """The active interface, validated against what's actually available.

        Never report a local model that isn't currently detected (that was the
        '已激活 Ollama' ghost when no local model exists)."""
        online = backend.get_active_model(use_online=True)
        local = backend.get_active_model(use_online=False)
        try:
            local_models = backend.scan_local_models()
        except Exception:  # noqa: BLE001
            local_models = []
        online_names = [i["name"] for i in backend.list_online_interfaces()]

        # Local mode only if a real, detected local model is selected.
        if not backend.get_config("default_online", True):
            if local and local in local_models:
                return local
        # Otherwise fall back to a valid online interface.
        if online and online in online_names:
            return online
        return online_names[0] if online_names else (
            local if local in local_models else "")

    def reload(self):
        active = self._active_name()
        self.active_pill.setText(f"{tr('Active', self._lang)}: {active or '-'}")

        # Local
        self.local_group.clear()
        try:
            local_models = backend.scan_local_models()
        except Exception:  # noqa: BLE001 - backend probing is best-effort
            local_models = []
        if not local_models:
            self.local_group.add_entry(BodyLabel(tr("No local models", self._lang)))
        for name in local_models:
            card = EntryCard(name, "Ollama / LM Studio", FluentIcon.IOT,
                             active=(name == active))
            card.clicked.connect(lambda n=name: self._set_active(n, online=False))
            self.local_group.add_entry(card)

        # Official + Custom
        self.official_group.clear()
        self.custom_group.clear()
        for itf in backend.list_online_interfaces():
            name = itf["name"]
            card = EntryCard(name, itf.get("model", ""), FluentIcon.CLOUD,
                             active=(name == active))
            card.clicked.connect(lambda n=name: self._set_active(n, online=True))
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda _pos, n=name: self._entry_menu(n))
            if itf["official"]:
                self.official_group.add_entry(card)
            else:
                self.custom_group.add_entry(card)

    def _entry_menu(self, name):
        menu = RoundMenu(parent=self)
        menu.addAction(Action(FluentIcon.ACCEPT, tr("Set Active", self._lang),
                              triggered=lambda: self._set_active(name, online=True)))
        menu.addAction(Action(FluentIcon.EDIT, tr("Edit Params", self._lang),
                              triggered=lambda: self.on_add(existing=name)))
        menu.addAction(Action(FluentIcon.DELETE, tr("Delete", self._lang),
                              triggered=lambda: self._delete(name)))
        menu.exec(QCursor.pos())

    def _set_active(self, name, online):
        backend.set_active_model(name, use_online=online)
        backend.set_config("default_online", online)
        self.reload()
        if callable(self.on_active_changed):
            self.on_active_changed()
        if not online:
            # Local models translate noticeably worse; warn the user.
            self._info(tr("Local Model Warning", self._lang), error=True)
        else:
            self._info(tr("Active", self._lang) + f": {name}")

    def _delete(self, name):
        if backend.delete_api_config(name):
            self.reload()
            self._info(tr("Interface deleted", self._lang))

    def on_add(self, existing=None):
        dlg = AddInterfaceDialog(self, lang=self._lang, existing=existing)
        if not dlg.exec():
            return
        name, data = dlg.values()
        if not name:
            self._info(tr("Interface Name", self._lang), error=True)
            return
        try:
            backend.write_api_config(name, data)
            # Persist the key per provider in mykeys/ (shared with the Web app).
            save_api_key_for_model(name, dlg.api_key())
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self.reload()
        if callable(self.on_active_changed):
            self.on_active_changed()
        self._info(tr("Interface saved", self._lang))

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Interface Management", self._lang), text, orient=1,
            isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=self)
