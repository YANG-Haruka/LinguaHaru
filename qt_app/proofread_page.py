"""Proofread page: edit a finished translation's segments and re-export.

Mirrors the web Proofread tab. A ComboBox lists proofreadable docs (finished
translations with dst_translated.json + manifest.json, PDF excluded). Loading a
doc fills an editable table (count_src | original [read-only] | translated
[editable]). Save writes the edited translations back; Re-export regenerates the
document via the original-format writer. All proofread logic is pure and lives
in qt_app.backend.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, TableWidget, BodyLabel,
    StrongBodyLabel, InfoBar, InfoBarPosition, FluentIcon, ToolButton,
)

from qt_app import backend
from qt_app.i18n import tr
from qt_app.history_page import open_folder


class ProofreadPage(QWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("ProofreadPage")
        self._lang = lang
        self._doc_name = None
        self._last_output_dir = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        self.title = StrongBodyLabel(tr("Proofread", lang))
        layout.addWidget(self.title)

        top = QHBoxLayout()
        self.doc_label = BodyLabel(tr("Proofread Document", lang) + ":")
        top.addWidget(self.doc_label)
        self.combo = ComboBox()
        self.combo.setMinimumWidth(280)
        top.addWidget(self.combo, 1)
        self.refresh_btn = ToolButton(FluentIcon.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_docs)
        top.addWidget(self.refresh_btn)
        self.load_btn = PushButton(FluentIcon.VIEW, tr("Load Glossary", lang))
        self.load_btn.clicked.connect(self.on_load)
        top.addWidget(self.load_btn)
        layout.addLayout(top)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)

        action_row = QHBoxLayout()
        self.save_btn = PrimaryPushButton(FluentIcon.SAVE, tr("Save Edits", lang))
        self.save_btn.clicked.connect(self.on_save)
        self.export_btn = PushButton(FluentIcon.SHARE, tr("Re-export", lang))
        self.export_btn.clicked.connect(self.on_export)
        self.open_output_btn = PushButton(FluentIcon.FOLDER, tr("Open Output Folder", lang))
        self.open_output_btn.setEnabled(False)
        self.open_output_btn.clicked.connect(
            lambda: open_folder(self._last_output_dir))
        action_row.addWidget(self.save_btn)
        action_row.addWidget(self.export_btn)
        action_row.addWidget(self.open_output_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.status_label = BodyLabel("")
        layout.addWidget(self.status_label)

        self.refresh_docs()

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Proofread", lang))
        self.doc_label.setText(tr("Proofread Document", lang) + ":")
        self.load_btn.setText(tr("Load Glossary", lang))
        self.save_btn.setText(tr("Save Edits", lang))
        self.export_btn.setText(tr("Re-export", lang))
        self.open_output_btn.setText(tr("Open Output Folder", lang))
        self._relabel_table_headers()

    def _relabel_table_headers(self):
        if self.table.columnCount() == 3:
            self.table.setHorizontalHeaderLabels([
                "count_src",
                tr("Original Text", self._lang),
                tr("Translated Text", self._lang),
            ])

    def refresh_docs(self):
        current = self.combo.currentText() if self.combo.count() else None
        self.combo.clear()
        docs = backend.list_proofread_docs()
        self.combo.addItems(docs)
        if current and current in docs:
            self.combo.setCurrentText(current)
        if not docs:
            self.status_label.setText(tr("No proofread documents", self._lang))

    def on_load(self):
        name = self.combo.currentText()
        if not name:
            return
        try:
            rows = backend.load_proofread_table(name)
        except Exception as e:  # noqa: BLE001
            self._info(f"{e}", error=True)
            return
        self._doc_name = name
        self.table.setColumnCount(3)
        self._relabel_table_headers()
        self.table.setRowCount(len(rows))
        for r, (count_src, original, translated) in enumerate(rows):
            count_item = QTableWidgetItem("" if count_src is None else str(count_src))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            orig_item = QTableWidgetItem(original)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, count_item)
            self.table.setItem(r, 1, orig_item)
            self.table.setItem(r, 2, QTableWidgetItem(translated))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.status_label.setText(
            tr("Loaded entries", self._lang).format(count=len(rows), name=name))

    def _table_rows(self):
        rows = []
        for r in range(self.table.rowCount()):
            count_item = self.table.item(r, 0)
            trans_item = self.table.item(r, 2)
            count = count_item.text() if count_item else ""
            try:
                count = int(count)
            except (TypeError, ValueError):
                pass
            rows.append((count, "", trans_item.text() if trans_item else ""))
        return rows

    def on_save(self):
        if not self._doc_name:
            self._info(tr("No proofread documents", self._lang), error=True)
            return
        try:
            changed = backend.save_proofread_table(self._doc_name, self._table_rows())
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        msg = tr("Edits saved", self._lang).format(count=changed)
        self.status_label.setText(msg)
        self._info(msg)

    def on_export(self):
        if not self._doc_name:
            self._info(tr("No proofread documents", self._lang), error=True)
            return
        try:
            out_path = backend.export_proofread_doc(self._doc_name)
        except Exception as e:  # noqa: BLE001
            self._info(str(e), error=True)
            return
        self._last_output_dir = os.path.dirname(out_path)
        self.open_output_btn.setEnabled(True)
        msg = tr("Export completed", self._lang)
        self.status_label.setText(f"{msg}: {os.path.basename(out_path)}")
        self._info(msg)

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Proofread", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)
