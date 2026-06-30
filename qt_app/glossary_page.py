"""Glossary editor page: pick a glossary, edit it in a table, Load / Save.

Save refuses to overwrite a non-empty file with an empty table (web guard,
enforced in backend.save_glossary)."""


import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView, QFileDialog,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, TableWidget, BodyLabel,
    StrongBodyLabel, InfoBar, InfoBarPosition, FluentIcon,
    MessageBoxBase, MessageBox, SubtitleLabel, LineEdit,
)

from core import backend
from qt_app.i18n import tr


class _NameDialog(MessageBoxBase):
    """Small dialog to enter a name for a new / imported glossary."""

    def __init__(self, parent=None, lang="en", default="", title_key="New Glossary"):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(tr(title_key, lang), self)
        self.name_edit = LineEdit(self)
        self.name_edit.setText(default)
        self.name_edit.setPlaceholderText("my-glossary")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.name_edit)
        self.yesButton.setText(tr("Save", lang))
        self.cancelButton.setText(tr("Cancel", lang))
        self.widget.setMinimumWidth(360)

    def name(self):
        return self.name_edit.text().strip()


class GlossaryPage(QWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("GlossaryPage")
        self._lang = lang
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        self.title = StrongBodyLabel(tr("Edit Glossary", lang))
        layout.addWidget(self.title)

        top = QHBoxLayout()
        self.glossary_label = BodyLabel(tr("Glossary", lang) + ":")
        top.addWidget(self.glossary_label)
        self.combo = ComboBox()
        self.combo.setFixedWidth(160)
        self.refresh_combo()
        top.addWidget(self.combo)
        self.new_btn = PushButton(FluentIcon.ADD, tr("New Glossary", lang))
        self.new_btn.clicked.connect(self.on_new)
        self.import_btn = PushButton(FluentIcon.FOLDER, tr("Import Glossary", lang))
        self.import_btn.clicked.connect(self.on_import)
        self.delete_btn = PushButton(FluentIcon.DELETE, tr("Delete", lang))
        self.delete_btn.clicked.connect(self.on_delete)
        top.addWidget(self.new_btn)
        top.addWidget(self.import_btn)
        top.addWidget(self.delete_btn)
        top.addStretch(1)
        self.load_btn = PushButton(FluentIcon.SYNC, tr("Load Glossary", lang))
        self.load_btn.clicked.connect(self.on_load)
        self.save_btn = PrimaryPushButton(FluentIcon.SAVE, tr("Save Glossary", lang))
        self.save_btn.clicked.connect(self.on_save)
        top.addWidget(self.load_btn)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        # Show full cell content with a horizontal scrollbar rather than
        # squeezing every column to fit (which truncated the text).
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.table, 1)

        self._header = []
        if self.combo.count():
            self.on_load()

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Edit Glossary", lang))
        self.glossary_label.setText(tr("Glossary", lang) + ":")
        self.new_btn.setText(tr("New Glossary", lang))
        self.import_btn.setText(tr("Import Glossary", lang))
        self.delete_btn.setText(tr("Delete", lang))
        self.load_btn.setText(tr("Load Glossary", lang))
        self.save_btn.setText(tr("Save Glossary", lang))

    def refresh_combo(self):
        current = self.combo.currentText() if self.combo.count() else None
        self.combo.clear()
        names = backend.get_glossary_files()
        self.combo.addItems(names)
        if current and current in names:
            self.combo.setCurrentText(current)

    def on_load(self):
        name = self.combo.currentText()
        if not name:
            return
        try:
            header, rows = backend.load_glossary(name)
        except Exception as e:  # noqa: BLE001
            self._info(f"Failed to load: {e}", error=True)
            return
        self._header = header or ["source", "target"]
        self.table.setColumnCount(len(self._header))
        self.table.setHorizontalHeaderLabels(self._header)
        # spare blank rows so the user can append entries
        self.table.setRowCount(len(rows) + 5)
        for r, row in enumerate(rows):
            for c in range(len(self._header)):
                val = row[c] if c < len(row) else ""
                self.table.setItem(r, c, QTableWidgetItem(val))
        # Size columns to their content (with a sensible minimum) so long terms
        # stay fully visible; the table scrolls horizontally when they overflow.
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(90)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        self._info(f"Loaded {len(rows)} entries from {name}.csv")

    def _table_rows(self):
        rows = []
        for r in range(self.table.rowCount()):
            row = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                row.append(item.text() if item else "")
            rows.append(row)
        return rows

    def on_save(self):
        name = self.combo.currentText()
        if not name:
            return
        try:
            count = backend.save_glossary(name, self._header, self._table_rows())
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self._info(f"Saved {count} entries to {name}.csv")

    def on_new(self):
        dlg = _NameDialog(self, self._lang)
        if not dlg.exec():
            return
        name = dlg.name()
        if not name:
            return
        try:
            backend.create_glossary(name)
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self._select_and_load(name)

    def on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import Glossary", self._lang), "",
            "CSV (*.csv);;All files (*.*)")
        if not path:
            return
        default = os.path.splitext(os.path.basename(path))[0]
        dlg = _NameDialog(self, self._lang, default=default, title_key="Import Glossary")
        if not dlg.exec():
            return
        name = dlg.name()
        if not name:
            return
        try:
            backend.import_glossary(name, path)
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self._select_and_load(name)

    def on_delete(self):
        name = self.combo.currentText()
        if not name:
            return
        msg = tr("Delete Glossary Confirm", self._lang).replace("{name}", name)
        if not MessageBox(tr("Delete", self._lang), msg, self).exec():
            return
        try:
            backend.delete_glossary(name)
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self.refresh_combo()
        if self.combo.count():
            self.on_load()
        else:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
        self._notify_translate()
        self._info(f"Deleted {name}.csv")

    def _select_and_load(self, name):
        """After create/import: refresh the picker, select the new glossary, load
        it, and keep the Translate page's glossary dropdown in sync."""
        self.refresh_combo()
        self.combo.setCurrentText(name)
        self.on_load()
        self._notify_translate()

    def _notify_translate(self):
        """Refresh the Translate page's glossary dropdown so a newly created /
        deleted glossary shows up there immediately."""
        mw = self.window()
        tp = getattr(mw, "translate_page", None)
        if tp is not None and hasattr(tp, "refresh_glossaries"):
            tp.refresh_glossaries()

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Glossary", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self)
