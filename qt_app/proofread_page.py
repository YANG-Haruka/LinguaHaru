"""Proofread page: edit a finished translation's segments and re-export.

Mirrors the web Proofread tab. A ComboBox lists proofreadable docs (finished
translations with dst_translated.json + manifest.json; PDF included — it
re-renders via BabelDOC from the edited text). Loading a doc fills an editable
table (count_src | original [read-only] | translated [editable]). Save writes the
edited translations back; Re-export regenerates the document (original-format
writer, or a BabelDOC re-render for PDF). All proofread logic lives in
core.backend.
"""

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem, QHeaderView,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, TableWidget, BodyLabel,
    StrongBodyLabel, InfoBar, InfoBarPosition, FluentIcon, ToolButton,
)

from core import backend
from qt_app.i18n import tr
from qt_app.history_page import open_folder


class _ExportWorker(QThread):
    """Re-export a proofread doc off the UI thread (PDF re-renders via BabelDOC,
    which is slow). Emits the output path or an error message."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, doc_name, parent=None):
        super().__init__(parent)
        self._doc_name = doc_name

    def run(self):
        try:
            self.done.emit(backend.export_proofread_doc(self._doc_name))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ProofreadPage(QWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("ProofreadPage")
        self._lang = lang
        self._doc_name = None
        self._last_output_dir = None
        self._all_rows = []        # full document model: [(count, original, translated)]
        self._page = 0
        self._capturing = False    # guard so populating the table isn't seen as edits
        self._refreshing = False   # guard so repopulating the combo doesn't auto-load
        self._sort_by = "time"     # "time" | "name"; document-list sort
        self._sort_desc = True     # time: newest first; name: A->Z is desc=False
        self.PAGE = 100

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
        # Selecting a doc loads it immediately (matches the web Proofread tab,
        # which has no separate Load button) — the old manual "Load" button was
        # mislabeled "Load Glossary" and made it look like nothing happened.
        self.combo.currentIndexChanged.connect(self._on_doc_selected)
        top.addWidget(self.combo, 1)
        # Document-list sort toggles (left of refresh): time (newest<->oldest) and
        # name (A->Z <-> Z->A). Clicking the active one flips its direction;
        # clicking the other switches sort mode to its default direction.
        self.time_sort_btn = ToolButton(FluentIcon.DATE_TIME)
        self.time_sort_btn.clicked.connect(lambda: self._toggle_sort("time"))
        top.addWidget(self.time_sort_btn)
        self.name_sort_btn = ToolButton(FluentIcon.FONT)
        self.name_sort_btn.clicked.connect(lambda: self._toggle_sort("name"))
        top.addWidget(self.name_sort_btn)
        self.refresh_btn = ToolButton(FluentIcon.SYNC)
        self.refresh_btn.clicked.connect(self.refresh_docs)
        top.addWidget(self.refresh_btn)
        layout.addLayout(top)
        self._update_sort_buttons()

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(True)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)

        # Pager: a big document is rendered 100 rows at a time.
        pager = QHBoxLayout()
        self.prev_btn = PushButton(FluentIcon.LEFT_ARROW, tr("Previous", lang))
        self.prev_btn.clicked.connect(lambda: self._goto(self._page - 1))
        self.next_btn = PushButton(FluentIcon.RIGHT_ARROW, tr("Next", lang))
        self.next_btn.clicked.connect(lambda: self._goto(self._page + 1))
        self.page_label = BodyLabel("")
        pager.addWidget(self.prev_btn)
        pager.addWidget(self.next_btn)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        self._pager_row = pager
        layout.addLayout(pager)

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

        # NOTE: no scan/auto-load here — the temp scan + first-doc render would run
        # while this page is built at startup (blocking the UI thread). MainWindow
        # calls refresh_docs() each time the user opens this page (_on_page_changed),
        # so loading happens lazily, only when actually needed.

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Proofread", lang))
        self.doc_label.setText(tr("Proofread Document", lang) + ":")
        self.save_btn.setText(tr("Save Edits", lang))
        self.export_btn.setText(tr("Re-export", lang))
        self.open_output_btn.setText(tr("Open Output Folder", lang))
        self.prev_btn.setText(tr("Previous", lang))
        self.next_btn.setText(tr("Next", lang))
        self._update_sort_buttons()
        self._relabel_table_headers()

    def _relabel_table_headers(self):
        if self.table.columnCount() == 3:
            self.table.setHorizontalHeaderLabels([
                "count_src",
                tr("Original Text", self._lang),
                tr("Translated Text", self._lang),
            ])

    def _toggle_sort(self, mode):
        """Clicking the active sort flips its direction; clicking the other switches
        mode to its natural default (time = newest first, name = A->Z)."""
        if self._sort_by == mode:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_by = mode
            self._sort_desc = (mode == "time")   # time->newest first; name->A->Z
        self._update_sort_buttons()
        self.refresh_docs()

    def _update_sort_buttons(self):
        arrow = "  ↓" if self._sort_desc else "  ↑"   # ↓ = desc (newest / Z->A)
        self.time_sort_btn.setToolTip(
            tr("Sort by Time", self._lang) + (arrow if self._sort_by == "time" else ""))
        self.name_sort_btn.setToolTip(
            tr("Sort by Name", self._lang) + (arrow if self._sort_by == "name" else ""))
        for btn, mode in ((self.time_sort_btn, "time"), (self.name_sort_btn, "name")):
            btn.setProperty("active", self._sort_by == mode)

    def refresh_docs(self):
        # Keep the current selection across a refresh; otherwise default to the
        # first doc. Repopulating fires currentIndexChanged, so guard against the
        # auto-load handler and load once explicitly at the end.
        prev = self._doc_name or (self.combo.currentText() if self.combo.count() else None)
        self._refreshing = True
        self.combo.clear()
        docs = backend.list_proofread_docs(sort_by=self._sort_by, descending=self._sort_desc)
        self.combo.addItems(docs)
        if prev and prev in docs:
            self.combo.setCurrentText(prev)
        self._refreshing = False
        if not docs:
            self.status_label.setText(tr("No proofread documents", self._lang))
            self.table.setRowCount(0)
            self._all_rows = []
            self._doc_name = None
            return
        self.on_load()   # load whatever is now selected (restored or first)

    def _on_doc_selected(self, _index):
        """Load the chosen doc as soon as the combo selection changes (web parity).
        Skipped while refresh_docs() is repopulating the combo."""
        if self._refreshing:
            return
        if self.combo.currentText():
            self.on_load()

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
        self._all_rows = [list(r) for r in rows]   # mutable: capture edits here
        self._page = 0
        self.table.setColumnCount(3)
        self._relabel_table_headers()
        self._render_page()
        self.status_label.setText(
            tr("Loaded entries", self._lang).format(count=len(rows), name=name))

    def _render_page(self):
        """Populate the table with the current 100-row slice."""
        self._capturing = True
        start = self._page * self.PAGE
        end = min(start + self.PAGE, len(self._all_rows))
        self.table.setRowCount(end - start)
        for i, idx in enumerate(range(start, end)):
            count_src, original, translated = self._all_rows[idx]
            count_item = QTableWidgetItem("" if count_src is None else str(count_src))
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            orig_item = QTableWidgetItem(original)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, count_item)
            self.table.setItem(i, 1, orig_item)
            self.table.setItem(i, 2, QTableWidgetItem(translated))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self._capturing = False
        self._update_pager(start, end)

    def _on_cell_changed(self, row, col):
        if self._capturing or col != 2:
            return
        idx = self._page * self.PAGE + row
        if 0 <= idx < len(self._all_rows):
            item = self.table.item(row, 2)
            self._all_rows[idx][2] = item.text() if item else ""

    def _goto(self, page):
        pages = max(1, (len(self._all_rows) + self.PAGE - 1) // self.PAGE)
        page = max(0, min(page, pages - 1))
        if page != self._page:
            self._page = page
            self._render_page()

    def _update_pager(self, start, end):
        total = len(self._all_rows)
        pages = max(1, (total + self.PAGE - 1) // self.PAGE)
        multi = total > self.PAGE
        for w in (self.prev_btn, self.next_btn, self.page_label):
            w.setVisible(multi)
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < pages - 1)
        self.page_label.setText(f"{self._page + 1}/{pages}  ({start + 1}-{end} / {total})")

    def on_save(self):
        if not self._doc_name:
            self._info(tr("No proofread documents", self._lang), error=True)
            return
        try:
            changed = backend.save_proofread_table(
                self._doc_name, [(c, o, t) for c, o, t in self._all_rows])
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
        # Re-export off the UI thread: a PDF re-export re-renders via BabelDOC and
        # would otherwise freeze the window for the whole render.
        self.export_btn.setEnabled(False)
        self.status_label.setText(tr("Re-export", self._lang) + "…")
        self._export_worker = _ExportWorker(self._doc_name, self)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.start()

    def _on_export_done(self, out_path):
        self.export_btn.setEnabled(True)
        self._last_output_dir = os.path.dirname(out_path)
        self.open_output_btn.setEnabled(True)
        msg = tr("Export completed", self._lang)
        self.status_label.setText(f"{msg}: {os.path.basename(out_path)}")
        self._info(msg)

    def _on_export_failed(self, err):
        self.export_btn.setEnabled(True)
        self.status_label.setText("")
        self._info(err, error=True)

    def _info(self, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(tr("Proofread", self._lang), text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)
