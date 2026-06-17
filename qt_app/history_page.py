"""History page: recent translations from TranslationHistoryManager.

Layout: a compact table (status / file / type / time) keeps the list scannable;
clicking a row fills a detail panel with the full record; right-clicking a row
opens a menu — Open Folder (always), Continue Translation (interrupted runs
only), Delete (record + all of its data). Interrupted runs (failed/stopped) are
shown too, and can be resumed via continue_mode.
"""

import os
import json
import shutil
import subprocess
import platform
from datetime import datetime

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    TableWidget, PushButton, PrimaryPushButton, StrongBodyLabel, ComboBox,
    BodyLabel, FluentIcon, StateToolTip, MessageBox, SimpleCardWidget, CaptionLabel,
)

from core import backend
from core.api_keys import load_api_key_for_model
from qt_app.i18n import tr
from qt_app.worker import TranslationWorker
from core.translation_history import (
    TranslationHistoryManager, format_tokens, format_duration,
)

# Compact table columns (resolved per UI language). The full record shows in the
# detail panel below, so the table stays uncluttered (no truncated columns).
_COLUMN_KEYS = ["Status", "Upload File", "File Type", "Time"]
# (sort_by, descending) per sort option, aligned with _SORT_KEYS
_SORT_OPTIONS = [("start_time", True), ("start_time", False),
                 ("file_type", False), ("input_file", False), ("total_tokens", True)]
_SORT_KEYS = ["Newest", "Oldest", "By Type", "By Name", "By Tokens"]

# status -> (i18n key, color)
_STATUS_META = {
    "success": ("Status Success", "#2e7d32"),
    "failed": ("Status Failed", "#c62828"),
    "stopped": ("Status Stopped", "#ef6c00"),
    "interrupted": ("Status Interrupted", "#ef6c00"),
    "running": ("Status Running", "#1565c0"),
}


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


def _resume_info(rec):
    """Parsed resume_info dict, or {} if absent/malformed."""
    try:
        return json.loads(rec.get("resume_info") or "") or {}
    except (ValueError, TypeError):
        return {}


def _is_resumable(rec):
    return rec.get("status") in ("failed", "stopped", "interrupted") and bool(_resume_info(rec))


class HistoryPage(QWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("HistoryPage")
        self._lang = lang
        self._resume_worker = None
        self._tip = None
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
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)
        self._types_loaded = False

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(len(_COLUMN_KEYS))
        self._apply_headers()
        self.table.setSelectionBehavior(TableWidget.SelectRows)
        self.table.setSelectionMode(TableWidget.SingleSelection)
        self.table.setEditTriggers(TableWidget.NoEditTriggers)
        # Click a row -> show its detail panel (with inline action buttons).
        # itemClicked fires even when the row is ALREADY selected (e.g. the last
        # remaining row after a delete) — itemSelectionChanged alone would not, so
        # that row became un-clickable. Keep both: clicks + keyboard navigation.
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemClicked.connect(lambda *_: self._on_row_selected())
        layout.addWidget(self.table, 1)

        # Detail panel: full info for the selected row (hidden until a selection),
        # with inline action buttons (Open Folder / Continue / Delete) — same as
        # the web frontend, no right-click needed.
        self.detail_card = SimpleCardWidget()
        dlay = QVBoxLayout(self.detail_card)
        dlay.setContentsMargins(16, 12, 16, 12)
        dlay.setSpacing(4)
        self.detail_title = StrongBodyLabel("")
        self.detail_body = BodyLabel("")
        self.detail_body.setWordWrap(True)
        self.detail_hint = CaptionLabel("")
        dlay.addWidget(self.detail_title)
        dlay.addWidget(self.detail_body)
        dlay.addWidget(self.detail_hint)
        self.detail_acts = QHBoxLayout()
        self.detail_acts.setSpacing(8)
        self.detail_acts.setContentsMargins(0, 8, 0, 0)
        dlay.addLayout(self.detail_acts)
        self.detail_card.hide()
        layout.addWidget(self.detail_card)

        self._records = []
        self.reload()

    def _apply_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr(k, self._lang) for k in _COLUMN_KEYS])

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Translation History", lang))
        self.refresh_btn.setText(tr("Refresh Records", lang))
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
        self.detail_card.hide()
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
        # Repopulate without the row changes firing _on_row_selected mid-rebuild
        # (which made the panel "auto-jump" to another row after a delete).
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._records))
        for r, rec in enumerate(self._records):
            status = rec.get("status", "")
            key, color = _STATUS_META.get(status, (status, None))
            status_item = QTableWidgetItem(tr(key, self._lang) if key else status)
            if color:
                status_item.setForeground(QColor(color))
            cells = [
                status_item,
                QTableWidgetItem(rec.get("input_file", "")),
                QTableWidgetItem((rec.get("file_type", "") or "").upper()),
                QTableWidgetItem(self._fmt_time(rec.get("start_time", ""))),
            ]
            for c, item in enumerate(cells):
                self.table.setItem(r, c, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        # Clear any leftover selection so the next click is always a fresh one
        # (so even the last remaining row reliably opens its detail).
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.detail_card.hide()

    @staticmethod
    def _fmt_time(start):
        if not start:
            return "-"
        try:
            return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return start[:16]

    # --- detail panel ----------------------------------------------------- #
    def _on_row_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._records):
            self.detail_card.hide()
            return
        rec = self._records[row]
        L = self._lang
        langs = f"{rec.get('src_lang_display', rec.get('src_lang', ''))} → " \
                f"{rec.get('dst_lang_display', rec.get('dst_lang', ''))}"
        mode = "Online" if rec.get("use_online") else "Offline"
        cost = (f"{rec.get('cost_amount')} {rec.get('cost_currency')}"
                if rec.get("cost_amount") is not None and rec.get("cost_currency") else "-")
        dur = format_duration(int(rec.get("duration_seconds") or 0))
        rows = [
            (tr("Source Language", L), langs),
            (tr("Model", L), f"{rec.get('model', '')} ({mode})"),
            (tr("Tokens", L), format_tokens(rec.get("total_tokens", 0))),
            (tr("Estimated cost", L), cost),
            (tr("Duration", L), dur),
            (tr("Output File", L), rec.get("output_file_path") or "-"),
        ]
        if rec.get("error_reason"):
            rows.append((tr("Error Reason", L), rec.get("error_reason")))
        self.detail_title.setText(rec.get("input_file", ""))
        self.detail_body.setText(
            "\n".join(f"<b>{k}:</b> {v}" for k, v in rows).replace("\n", "<br>"))
        self.detail_hint.setText(tr("Resume Hint", L) if _is_resumable(rec) else "")
        self._build_detail_actions(rec)
        self.detail_card.show()

    def _build_detail_actions(self, rec):
        """(Re)build the inline action buttons for the selected record: Open
        Folder (always), Continue (interrupted only), Delete — same as web, no
        right-click needed."""
        # Clear any buttons from the previous selection.
        while self.detail_acts.count():
            item = self.detail_acts.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        L = self._lang
        open_btn = PushButton(FluentIcon.FOLDER, tr("Open Folder", L))
        open_btn.clicked.connect(lambda: self._open_record_folder(rec))
        self.detail_acts.addWidget(open_btn)
        if _is_resumable(rec):
            cont_btn = PrimaryPushButton(FluentIcon.PLAY, tr("Continue Translation", L))
            cont_btn.clicked.connect(lambda: self._continue_record(rec))
            self.detail_acts.addWidget(cont_btn)
        del_btn = PushButton(FluentIcon.DELETE, tr("Delete Record", L))
        del_btn.clicked.connect(lambda: self._delete_record(rec))
        self.detail_acts.addWidget(del_btn)
        self.detail_acts.addStretch(1)

    def _open_record_folder(self, rec):
        # Prefer the output folder; fall back to the result dir from resume_info.
        out = rec.get("output_file_path") or ""
        if out:
            open_folder(os.path.dirname(out))
            return
        info = _resume_info(rec)
        if info.get("result_dir"):
            open_folder(info["result_dir"])

    # --- delete (record + all data) --------------------------------------- #
    def _delete_record(self, rec):
        L = self._lang
        box = MessageBox(tr("Delete Record", L), tr("Delete Record Confirm", L), self.window())
        if not box.exec():
            return
        # 1) Output + log files we generated (never the user's original input).
        for key in ("output_file_path", "log_file_path"):
            p = rec.get(key)
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        # 2) The per-file temp working dir (partial JSON / extracted media).
        info = _resume_info(rec)
        temp_dir = info.get("temp_dir")
        src = info.get("input_file_path") or rec.get("input_file")
        if temp_dir and src:
            file_dir = os.path.join(temp_dir, os.path.splitext(os.path.basename(src))[0])
            if os.path.isdir(file_dir):
                shutil.rmtree(file_dir, ignore_errors=True)
        # 3) The DB row.
        _, _, log_dir = backend.get_custom_paths()
        TranslationHistoryManager(log_dir=log_dir).delete_record(rec.get("id"))
        self.reload()

    # --- continue (resume) ------------------------------------------------ #
    def _continue_record(self, rec):
        L = self._lang
        if self._resume_worker is not None:
            return  # one resume at a time
        info = _resume_info(rec)
        if not info:
            self._toast(tr("No Resume Info", L), error=True)
            return
        src = info.get("input_file_path") or rec.get("input_file")
        if not src or not os.path.exists(src):
            self._toast(tr("Source File Missing", L), error=True)
            return
        model = info.get("model") or rec.get("model")
        use_online = info.get("use_online", rec.get("use_online", True))
        api_key = load_api_key_for_model(model) if use_online else ""
        config = backend.read_config()
        resume_dirs = (info.get("temp_dir"), info.get("result_dir"), info.get("log_dir"))
        if not all(resume_dirs):
            self._toast(tr("No Resume Info", L), error=True)
            return

        worker = TranslationWorker(
            file_path=src, model=model, use_online=use_online, api_key=api_key,
            src_lang=info.get("src_lang", rec.get("src_lang_display", "")),
            dst_lang=info.get("dst_lang", rec.get("dst_lang_display", "")),
            max_token=info.get("max_token", config.get("max_token", 768)),
            max_retries=info.get("max_retries", config.get("max_retries", 4)),
            thread_count=info.get("thread_count")
            or backend.thread_count_for_mode(use_online, model),
            glossary_name=info.get("glossary_name", ""),
            bilingual_flags=info.get("bilingual_flags", {}),
            session_lang=self._lang,
            continue_mode=True, resume_dirs=resume_dirs,
            resume_record_id=rec.get("id"),
        )
        # Prefer running on the Translate page's dashboard (web parity: continuing
        # a stopped run jumps back to the progress view). Falls back to the
        # in-place toast if no host is wired.
        host = getattr(self, "on_continue_resume", None)
        if callable(host) and host(worker, rec.get("input_file", "")):
            return
        worker.finished.connect(lambda *_: self._on_resume_done(True, ""))
        worker.failed.connect(lambda msg: self._on_resume_done(False, msg))
        self._resume_worker = worker
        self._tip = StateToolTip(
            tr("Resuming Translation", L), rec.get("input_file", ""), self.window())
        self._tip.move(self._tip.getSuitablePos())
        self._tip.show()
        worker.start()

    def _on_resume_done(self, ok, msg):
        L = self._lang
        if self._tip is not None:
            self._tip.setContent(tr("Resume Done", L) if ok else (msg or ""))
            self._tip.setState(True)   # auto-closes after a moment
            self._tip = None
        w = self._resume_worker
        self._resume_worker = None
        if w is not None:
            w.wait(2000)
        if not ok and msg:
            self._toast(msg, error=True)
        self.reload()

    def _toast(self, text, error=False):
        from qfluentwidgets import InfoBar
        (InfoBar.error if error else InfoBar.success)(
            title="", content=text, parent=self.window(), duration=4000)
