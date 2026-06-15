"""History page: recent translations from TranslationHistoryManager."""

import os
import subprocess
import platform
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    TableWidget, PushButton, StrongBodyLabel, ComboBox, BodyLabel, FluentIcon,
)

from core import backend
from qt_app.i18n import tr
from core.translation_history import (
    TranslationHistoryManager, format_tokens,
)

# label keys for the table header columns (resolved per UI language)
_COLUMN_KEYS = ["Status", "Time", "File Type", "Tokens", "Cost",
                "Source Language", "Model", "Upload File"]
# (sort_by, descending) per sort option, aligned with _SORT_KEYS
_SORT_OPTIONS = [("start_time", True), ("start_time", False),
                 ("file_type", False), ("input_file", False), ("total_tokens", True)]
_SORT_KEYS = ["Newest", "Oldest", "By Type", "By Name", "By Tokens"]


def open_folder(path):
    if not path:
        return
    # Normalize to an absolute, OS-native path. The saved paths can be relative
    # or have mixed slashes (e.g. "...\data/result"); Windows Explorer silently
    # falls back to the Documents folder when given such a malformed argument.
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)  # noqa: S606 - canonical "open this folder" on Windows
        elif system == "Darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


class HistoryPage(QWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("HistoryPage")
        self._lang = lang
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        top = QHBoxLayout()
        self.title = StrongBodyLabel(tr("Translation History", lang))
        top.addWidget(self.title)
        top.addStretch(1)
        # Browse controls: filter by file type, sort by time/type/name.
        self.type_label = BodyLabel(tr("File Type", lang))
        top.addWidget(self.type_label)
        self.type_combo = ComboBox()
        self.type_combo.setMinimumWidth(120)
        self.type_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.type_combo)
        self.sort_label = BodyLabel(tr("Sort", lang))
        top.addWidget(self.sort_label)
        self.sort_combo = ComboBox()
        self.sort_combo.setMinimumWidth(140)
        self.sort_combo.addItems([tr(k, lang) for k in _SORT_KEYS])
        self.sort_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.sort_combo)
        self.refresh_btn = PushButton(FluentIcon.SYNC, tr("Refresh Records", lang))
        self.refresh_btn.clicked.connect(self.reload)
        self.open_btn = PushButton(FluentIcon.FOLDER, tr("Open Output Folder", lang))
        self.open_btn.clicked.connect(self.on_open_folder)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_btn)
        layout.addLayout(top)
        self._types_loaded = False

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(len(_COLUMN_KEYS))
        self._apply_headers()
        self.table.setSelectionBehavior(TableWidget.SelectRows)
        layout.addWidget(self.table, 1)

        self._records = []
        self.reload()

    def _apply_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr(k, self._lang) for k in _COLUMN_KEYS])

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Translation History", lang))
        self.refresh_btn.setText(tr("Refresh Records", lang))
        self.open_btn.setText(tr("Open Output Folder", lang))
        self.type_label.setText(tr("File Type", lang))
        self.sort_label.setText(tr("Sort", lang))
        sort_idx = self.sort_combo.currentIndex()
        self.sort_combo.blockSignals(True)
        self.sort_combo.clear()
        self.sort_combo.addItems([tr(k, lang) for k in _SORT_KEYS])
        self.sort_combo.setCurrentIndex(max(0, sort_idx))
        self.sort_combo.blockSignals(False)
        self._types_loaded = False   # rebuild "All Types" label in new language
        self._apply_headers()
        self.reload()

    def _on_filter_changed(self, _idx=0):
        self.reload()

    def reload(self):
        _, _, log_dir = backend.get_custom_paths()
        manager = TranslationHistoryManager(log_dir=log_dir)
        # Populate the file-type filter once (preserve current selection).
        if not self._types_loaded:
            cur = self.type_combo.currentData()
            self.type_combo.blockSignals(True)
            self.type_combo.clear()
            self.type_combo.addItem(tr("All Types", self._lang), userData="")
            for ft in manager.file_types():
                self.type_combo.addItem(ft.upper(), userData=ft)
            idx = max(0, self.type_combo.findData(cur)) if cur else 0
            self.type_combo.setCurrentIndex(idx)
            self.type_combo.blockSignals(False)
            self._types_loaded = True

        ftype = self.type_combo.currentData() or None
        sort_by, descending = _SORT_OPTIONS[max(0, self.sort_combo.currentIndex())]
        self._records = manager.get_all_records(
            limit=200, file_type=ftype, sort_by=sort_by, descending=descending)
        self.table.setRowCount(len(self._records))
        status_icon = {"success": "OK", "failed": "FAIL", "stopped": "STOP"}
        for r, rec in enumerate(self._records):
            start = rec.get("start_time", "")
            try:
                ts = datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M") if start else "-"
            except ValueError:
                ts = start[:16] if start else "-"
            langs = f"{rec.get('src_lang_display', rec.get('src_lang', ''))} -> " \
                    f"{rec.get('dst_lang_display', rec.get('dst_lang', ''))}"
            mode = "Online" if rec.get("use_online") else "Offline"
            cost = (f"{rec.get('cost_amount')} {rec.get('cost_currency')}"
                    if rec.get("cost_amount") is not None and rec.get("cost_currency") else "-")
            cells = [
                status_icon.get(rec.get("status", ""), rec.get("status", "")),
                ts,
                (rec.get("file_type", "") or "").upper(),
                format_tokens(rec.get("total_tokens", 0)),
                cost,
                langs,
                f"{rec.get('model', '')} ({mode})",
                rec.get("input_file", ""),
            ]
            for c, val in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(str(val)))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def on_open_folder(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._records):
            return
        path = self._records[row].get("output_file_path", "")
        if path:
            open_folder(os.path.dirname(path))
