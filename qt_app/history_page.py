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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTableWidgetItem, QHeaderView,
    QFrame, QLabel,
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
    "paused": ("Status Paused", "#8e8e93"),
    "queued": ("Status Queued", "#6b7280"),
}
# Order for the status filter dropdown.
_STATUS_FILTER = ["success", "failed", "stopped", "interrupted", "running", "paused", "queued"]


def _stat_block(label, value, accent=None):
    """A small rounded 'stat chip' (muted caption + bold value) for the detail
    panel — gives the record detail a card-block look instead of flat text."""
    f = QFrame()
    f.setObjectName("statBlock")
    f.setStyleSheet(
        "#statBlock{background:rgba(128,128,128,0.10);"
        "border:1px solid rgba(128,128,128,0.20);border-radius:10px;}")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 9, 14, 9)
    v.setSpacing(2)
    cap = CaptionLabel(label)
    cap.setStyleSheet("color:rgba(140,140,140,0.95);")
    val = StrongBodyLabel(str(value))
    val.setWordWrap(True)
    if accent:
        val.setStyleSheet(f"color:{accent};")
    v.addWidget(cap)
    v.addWidget(val)
    return f


def _status_pill(text, color):
    """A colored, rounded status badge."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{color or '#888'};color:white;border-radius:9px;"
        "padding:2px 12px;font-weight:600;")
    return lbl


def _agg_status(records):
    """Aggregate status for a batch: active (running/queued/paused) wins, else a
    terminal status (all-success / any-failed / stopped / interrupted)."""
    statuses = [r.get("status", "") for r in records]
    s = set(statuses)
    if s & {"running", "queued"}:
        return "running"
    if "paused" in s:
        return "paused"
    if statuses and all(x == "success" for x in statuses):
        return "success"
    for k in ("failed", "stopped", "interrupted"):
        if k in s:
            return k
    return statuses[0] if statuses else ""


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
        self.status_label = BodyLabel(tr("Status", lang))
        top.addWidget(self.status_label)
        self.status_combo = ComboBox()
        self.status_combo.setMinimumWidth(110)
        self._populate_status_combo()
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.status_combo)
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
        dlay.setContentsMargins(18, 14, 18, 14)
        dlay.setSpacing(10)
        # Header: title + colored status pill.
        head = QHBoxLayout()
        head.setSpacing(10)
        self.detail_title = StrongBodyLabel("")
        head.addWidget(self.detail_title)
        self.detail_status = QLabel("")
        self.detail_status.hide()
        head.addWidget(self.detail_status)
        head.addStretch(1)
        dlay.addLayout(head)
        # Stat-block grid (语言 / 模型 / Tokens / 费用 / 用时 / 输出).
        self.detail_grid = QGridLayout()
        self.detail_grid.setHorizontalSpacing(10)
        self.detail_grid.setVerticalSpacing(10)
        dlay.addLayout(self.detail_grid)
        # Per-file rows (multi-file batches only).
        self.detail_files = QVBoxLayout()
        self.detail_files.setSpacing(6)
        dlay.addLayout(self.detail_files)
        self.detail_hint = CaptionLabel("")
        self.detail_hint.setWordWrap(True)
        dlay.addWidget(self.detail_hint)
        self.detail_acts = QHBoxLayout()
        self.detail_acts.setSpacing(8)
        self.detail_acts.setContentsMargins(0, 4, 0, 0)
        dlay.addLayout(self.detail_acts)
        self.detail_card.hide()
        layout.addWidget(self.detail_card)

        self._records = []
        self._batches = []
        self.reload()

    def _apply_headers(self):
        self.table.setHorizontalHeaderLabels(
            [tr(k, self._lang) for k in _COLUMN_KEYS])

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Translation History", lang))
        self.refresh_btn.setText(tr("Refresh Records", lang))
        self.type_label.setText(tr("File Type", lang))
        self.status_label.setText(tr("Status", lang))
        self._populate_status_combo()   # relocalize status filter labels
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

    def _populate_status_combo(self):
        """All Statuses + one entry per known status (preserves the selection)."""
        cur = self.status_combo.currentData()
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        self.status_combo.addItem(tr("All Statuses", self._lang), userData="")
        for s in _STATUS_FILTER:
            self.status_combo.addItem(tr(_STATUS_META[s][0], self._lang), userData=s)
        idx = max(0, self.status_combo.findData(cur)) if cur else 0
        self.status_combo.setCurrentIndex(idx)
        self.status_combo.blockSignals(False)

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
        fstatus = self.status_combo.currentData() or None
        sort_by, descending = _SORT_OPTIONS[max(0, self.sort_combo.currentIndex())]
        self._records = manager.get_all_records(
            limit=400, file_type=ftype, sort_by=sort_by, descending=descending,
            status=fstatus)
        # Group into batches: files from ONE run (shared batch_id) fold into one
        # row. Each batch keeps first-seen order so sorting is preserved.
        self._batches = self._group_batches(self._records)
        # Repopulate without the row changes firing _on_row_selected mid-rebuild
        # (which made the panel "auto-jump" to another row after a delete).
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._batches))
        L = self._lang
        for r, recs in enumerate(self._batches):
            agg = _agg_status(recs)
            key, color = _STATUS_META.get(agg, (agg, None))
            status_item = QTableWidgetItem(tr(key, L) if key else agg)
            if color:
                status_item.setForeground(QColor(color))
            if len(recs) == 1:
                name = recs[0].get("input_file", "")
                ftype_txt = (recs[0].get("file_type", "") or "").upper()
            else:
                name = tr("Files Count", L).format(n=len(recs))
                types = {(rr.get("file_type", "") or "").upper() for rr in recs}
                ftype_txt = types.pop() if len(types) == 1 else "—"
            for c, item in enumerate((
                status_item,
                QTableWidgetItem(name),
                QTableWidgetItem(ftype_txt),
                QTableWidgetItem(self._fmt_time(recs[0].get("start_time", ""))),
            )):
                self.table.setItem(r, c, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.detail_card.hide()

    @staticmethod
    def _group_batches(records):
        """Group flat records into batches by batch_id (preserving order).
        A record with no batch_id (legacy / single) is its own batch."""
        order, by_key = [], {}
        for rec in records:
            bid = rec.get("batch_id") or ("__solo__" + str(rec.get("id")))
            if bid not in by_key:
                by_key[bid] = []
                order.append(bid)
            by_key[bid].append(rec)
        return [by_key[k] for k in order]

    @staticmethod
    def _fmt_time(start):
        if not start:
            return "-"
        try:
            return datetime.fromisoformat(start).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return start[:16]

    # --- detail panel ----------------------------------------------------- #
    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _set_status_pill(self, status):
        key, color = _STATUS_META.get(status, (status, None))
        self.detail_status.setText(tr(key, self._lang) if key else status)
        self.detail_status.setStyleSheet(
            f"background:{color or '#888'};color:white;border-radius:9px;"
            "padding:2px 12px;font-weight:600;")
        self.detail_status.show()

    def _fill_grid(self, pairs):
        for i, p in enumerate(pairs):
            block = _stat_block(p[0], p[1], p[2] if len(p) > 2 else None)
            self.detail_grid.addWidget(block, i // 2, i % 2)

    def _on_row_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._batches):
            self.detail_card.hide()
            return
        for lay in (self.detail_grid, self.detail_files, self.detail_acts):
            self._clear_layout(lay)
        recs = self._batches[row]
        if len(recs) == 1:
            self._show_single_detail(recs[0])
        else:
            self._show_batch_detail(recs)
        self.detail_card.show()

    def _show_single_detail(self, rec):
        L = self._lang
        self.detail_title.setText(rec.get("input_file", ""))
        self._set_status_pill(rec.get("status", ""))
        langs = (f"{rec.get('src_lang_display') or rec.get('src_lang', '')} → "
                 f"{rec.get('dst_lang_display') or rec.get('dst_lang', '')}")
        mode = "Online" if rec.get("use_online") else "Offline"
        cost = (f"{rec.get('cost_amount')} {rec.get('cost_currency')}"
                if rec.get("cost_amount") is not None and rec.get("cost_currency") else "—")
        self._fill_grid([
            (tr("Source Language", L), langs),
            (tr("Model", L), f"{rec.get('model', '')} ({mode})"),
            (tr("Tokens", L), format_tokens(rec.get("total_tokens", 0))),
            (tr("Estimated cost", L), cost),
            (tr("Duration", L), format_duration(int(rec.get("duration_seconds") or 0))),
            (tr("Output File", L), os.path.basename(rec.get("output_file_path") or "") or "—"),
        ])
        if rec.get("error_reason"):
            err = CaptionLabel("⚠ " + str(rec.get("error_reason"))[:300])
            err.setWordWrap(True)
            err.setStyleSheet("color:#c62828;")
            self.detail_files.addWidget(err)
        self.detail_hint.setText(tr("Resume Hint", L) if _is_resumable(rec) else "")
        # Actions
        self.detail_acts.addStretch(1)
        open_btn = PushButton(FluentIcon.FOLDER, tr("Open Folder", L))
        open_btn.clicked.connect(lambda: self._open_record_folder(rec))
        self.detail_acts.addWidget(open_btn)
        if _is_resumable(rec):
            cont = PrimaryPushButton(FluentIcon.PLAY, tr("Continue Translation", L))
            cont.clicked.connect(lambda: self._continue_record(rec))
            self.detail_acts.addWidget(cont)
        del_btn = PushButton(FluentIcon.DELETE, tr("Delete Record", L))
        del_btn.clicked.connect(lambda: self._delete_record(rec))
        self.detail_acts.addWidget(del_btn)

    def _show_batch_detail(self, recs):
        L = self._lang
        self.detail_title.setText(tr("Files Count", L).format(n=len(recs)))
        self._set_status_pill(_agg_status(recs))
        done = sum(1 for r in recs if r.get("status") == "success")
        total_tokens = sum(int(r.get("total_tokens") or 0) for r in recs)
        cost_amt = sum(float(r.get("cost_amount") or 0) for r in recs)
        ccy = next((r.get("cost_currency") for r in recs if r.get("cost_currency")), "")
        langs = (f"{recs[0].get('src_lang_display') or ''} → "
                 f"{recs[0].get('dst_lang_display') or ''}")
        self._fill_grid([
            (tr("Completed Files", L).replace("{done}", str(done)).replace("{total}", str(len(recs)))
             if "{done}" in tr("Completed Files", L) else f"{done}/{len(recs)}",
             f"{done}/{len(recs)}"),
            (tr("Source Language", L), langs),
            (tr("Model", L), recs[0].get("model", "")),
            (tr("Tokens", L), format_tokens(total_tokens)),
            (tr("Estimated cost", L), f"{cost_amt:.4f} {ccy}" if cost_amt else "—"),
            (tr("Duration", L), format_duration(
                sum(int(r.get("duration_seconds") or 0) for r in recs))),
        ])
        # Per-file rows: name + colored status pill + per-file continue.
        for r in recs:
            row = QFrame()
            row.setObjectName("fileRow")
            row.setStyleSheet("#fileRow{background:rgba(128,128,128,0.07);border-radius:8px;}")
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 5, 10, 5)
            h.setSpacing(8)
            nm = BodyLabel(r.get("input_file", ""))
            nm.setWordWrap(False)
            h.addWidget(nm)
            h.addStretch(1)
            key, color = _STATUS_META.get(r.get("status", ""), (r.get("status", ""), None))
            h.addWidget(_status_pill(tr(key, L) if key else r.get("status", ""), color))
            if _is_resumable(r):
                cb = PushButton(FluentIcon.PLAY, tr("Continue Translation", L))
                cb.clicked.connect(lambda _=False, rr=r: self._continue_record(rr))
                h.addWidget(cb)
            self.detail_files.addWidget(row)
        self.detail_hint.setText(
            tr("Resume Hint", L) if any(_is_resumable(r) for r in recs) else "")
        # Batch actions: continue all unfinished + open the run folder + delete.
        self.detail_acts.addStretch(1)
        if any(_is_resumable(r) for r in recs):
            cont_all = PrimaryPushButton(FluentIcon.PLAY, tr("Continue All", L))
            cont_all.clicked.connect(lambda: self._continue_batch(recs))
            self.detail_acts.addWidget(cont_all)
        open_btn = PushButton(FluentIcon.FOLDER, tr("Open Folder", L))
        open_btn.clicked.connect(lambda: self._open_batch_folder(recs))
        self.detail_acts.addWidget(open_btn)
        del_btn = PushButton(FluentIcon.DELETE, tr("Delete Record", L))
        del_btn.clicked.connect(lambda: self._delete_batch(recs))
        self.detail_acts.addWidget(del_btn)

    def _open_batch_folder(self, recs):
        for r in recs:
            out = r.get("output_file_path") or ""
            if out and os.path.exists(os.path.dirname(out)):
                open_folder(os.path.dirname(out))
                return
            info = _resume_info(r)
            if info.get("result_dir") and os.path.exists(info["result_dir"]):
                open_folder(info["result_dir"])
                return

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

    def _delete_batch(self, recs):
        """Delete a whole batch (all its files' records + generated files)."""
        L = self._lang
        box = MessageBox(tr("Delete Record", L), tr("Delete Record Confirm", L), self.window())
        if not box.exec():
            return
        _, _, log_dir = backend.get_custom_paths()
        mgr = TranslationHistoryManager(log_dir=log_dir)
        for rec in recs:
            for key in ("output_file_path", "log_file_path"):
                p = rec.get(key)
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            info = _resume_info(rec)
            temp_dir = info.get("temp_dir")
            src = info.get("input_file_path") or rec.get("input_file")
            if temp_dir and src:
                file_dir = os.path.join(temp_dir, os.path.splitext(os.path.basename(src))[0])
                if os.path.isdir(file_dir):
                    shutil.rmtree(file_dir, ignore_errors=True)
            mgr.delete_record(rec.get("id"))
        self.reload()

    # --- continue (resume) ------------------------------------------------ #
    def _build_resume_worker(self, rec):
        """Build a TranslationWorker from a history record, or None if it can't
        be resumed (no resume info / source file gone). A 'queued' row that never
        started has no dirs -> run fresh (continue_mode off) reusing its id."""
        info = _resume_info(rec)
        if not info:
            return None
        src = info.get("input_file_path") or rec.get("input_file")
        if not src or not os.path.exists(src):
            return None
        model = info.get("model") or rec.get("model")
        use_online = info.get("use_online", rec.get("use_online", True))
        api_key = load_api_key_for_model(model) if use_online else ""
        config = backend.read_config()
        resume_dirs = (info.get("temp_dir"), info.get("result_dir"), info.get("log_dir"))
        fresh = not all(resume_dirs)
        return TranslationWorker(
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
            continue_mode=not fresh, resume_dirs=None if fresh else resume_dirs,
            resume_record_id=rec.get("id"),
            batch_id=rec.get("batch_id") or None,
            batch_size=rec.get("batch_size") or 1,
        )

    def _continue_batch(self, recs):
        """Resume ALL resumable files of a batch at once, on the Translate
        dashboard (one run, normal concurrency)."""
        L = self._lang
        workers = [w for w in (self._build_resume_worker(r)
                               for r in recs if _is_resumable(r)) if w is not None]
        if not workers:
            self._toast(tr("Source File Missing", L), error=True)
            return
        host = getattr(self, "on_continue_resume_batch", None)
        if callable(host) and host(workers):
            return
        # Fallback: no dashboard host — resume them one at a time in place.
        for w in workers:
            w.finished.connect(lambda *_: self._on_resume_done(True, ""))
            w.failed.connect(lambda msg: self._on_resume_done(False, msg))
            w.start()

    def _continue_record(self, rec):
        L = self._lang
        if self._resume_worker is not None:
            return  # one resume at a time
        worker = self._build_resume_worker(rec)
        if worker is None:
            self._toast(tr("Source File Missing", L), error=True)
            return
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
