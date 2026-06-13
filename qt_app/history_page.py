"""History page: recent translations from TranslationHistoryManager."""

import os
import subprocess
import platform
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    TableWidget, PushButton, StrongBodyLabel, FluentIcon,
)

from qt_app import backend
from qt_app.i18n import tr
from config.translation_history import (
    TranslationHistoryManager, format_duration, format_tokens,
)

# label keys for the table header columns (resolved per UI language)
_COLUMN_KEYS = ["Status", "Time", "Duration", "Tokens",
                "Source Language", "Model", "Upload File"]


def open_folder(path):
    if not path or not os.path.exists(path):
        return
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["explorer", path], check=False)
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
        self.refresh_btn = PushButton(FluentIcon.SYNC, tr("Refresh Records", lang))
        self.refresh_btn.clicked.connect(self.reload)
        self.open_btn = PushButton(FluentIcon.FOLDER, tr("Open Output Folder", lang))
        self.open_btn.clicked.connect(self.on_open_folder)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_btn)
        layout.addLayout(top)

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
        self._apply_headers()

    def reload(self):
        _, _, log_dir = backend.get_custom_paths()
        manager = TranslationHistoryManager(log_dir=log_dir)
        self._records = manager.get_all_records(limit=50)
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
            cells = [
                status_icon.get(rec.get("status", ""), rec.get("status", "")),
                ts,
                format_duration(rec.get("duration_seconds", 0)),
                format_tokens(rec.get("total_tokens", 0)),
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
