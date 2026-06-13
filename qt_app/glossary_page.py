"""Glossary editor page: pick a glossary, edit it in a table, Load / Save.

Save refuses to overwrite a non-empty file with an empty table (web guard,
enforced in backend.save_glossary)."""

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, TableWidget, BodyLabel,
    StrongBodyLabel, InfoBar, InfoBarPosition, FluentIcon,
)

from qt_app import backend


class GlossaryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GlossaryPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        layout.addWidget(StrongBodyLabel("Glossary Editor"))

        top = QHBoxLayout()
        top.addWidget(BodyLabel("Glossary:"))
        self.combo = ComboBox()
        self.combo.setMinimumWidth(220)
        self.refresh_combo()
        top.addWidget(self.combo)
        top.addStretch(1)
        self.load_btn = PushButton(FluentIcon.SYNC, "Load")
        self.load_btn.clicked.connect(self.on_load)
        self.save_btn = PrimaryPushButton(FluentIcon.SAVE, "Save")
        self.save_btn.clicked.connect(self.on_save)
        top.addWidget(self.load_btn)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        layout.addWidget(self.table, 1)

        self._header = []
        if self.combo.count():
            self.on_load()

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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
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

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar("Glossary", text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self)
