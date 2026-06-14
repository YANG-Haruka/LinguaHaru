"""Translate page: pick files, choose languages/model/glossary, translate.

The page is a QStackedWidget with two views:
  - the controls view (colorful format-category header + file picker, languages,
    model card, bilingual toggles, action buttons);
  - the progress dashboard (grid of metric cards) shown while a run is active.

Multi-file runs translate concurrently with a bounded pool (size = min(file
count, backend.MAX_CONCURRENT_TASKS)). Each file runs on its own
TranslationWorker (QThread); files that share a base name are isolated into a
per-run subdir to avoid temp/result collisions. Per-file progress is aggregated
into the dashboard. On completion a results summary is shown (and, for multi-file
runs, a zip with a results.txt is produced). Stop cancels all in-flight files.
"""

import os
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFormLayout, QStackedWidget,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, ProgressBar, SwitchButton,
    BodyLabel, CaptionLabel, CardWidget, TitleLabel,
    InfoBar, InfoBarPosition, FluentIcon, ToolButton, PasswordLineEdit,
    ScrollArea, FlowLayout,
)

from qt_app import backend
from qt_app.i18n import tr
from qt_app.worker import TranslationWorker
from qt_app.history_page import open_folder
from qt_app.widgets import FormatCategoryCard
from qt_app.progress_dashboard import ProgressDashboard

# Colorful format categories (label-key, formats, hex color, icon).
_FORMAT_CATEGORIES = [
    ("Books", "EPUB · TXT", "#7c3aed", FluentIcon.LIBRARY),
    ("Documents", "DOCX · MD · PPTX · XLSX", "#2563eb", FluentIcon.DOCUMENT),
    ("Subtitles", "SRT · ASS · VTT · LRC", "#0891b2", FluentIcon.MOVIE),
    ("Data", "CSV · JSON · TSV", "#16a34a", FluentIcon.TILES),
    ("Web", "HTML · ODT", "#ea580c", FluentIcon.GLOBE),
    ("Complex", "PDF", "#dc2626", FluentIcon.CERTIFICATE),
]


class TranslatePage(QStackedWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("TranslatePage")
        self._lang = lang
        self._files = []
        self._workers = []          # active TranslationWorker list
        self._progress = {}         # worker -> last fraction (for aggregation)
        self._last_output_dir = None
        self._bilingual_switches = {}  # config-key -> SwitchButton
        # multi-file run state
        self._queue = []
        self._results = []          # successful output paths
        self._file_results = []     # (name, status, detail)
        self._run_subdir = None
        self._running = False
        self._total = 0
        self._tokens = 0
        self._fmt_cards = []

        # --- controls view (scrollable) ---
        self._controls = ScrollArea()
        self._controls.setWidgetResizable(True)
        # Never scroll horizontally: keep all rows inside the viewport width
        # so nothing gets cut off at the right edge.
        self._controls.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._controls.enableTransparentBackground()
        controls_host = QWidget()
        controls_host.setObjectName("translateControlsHost")
        self._controls.setWidget(controls_host)
        layout = QVBoxLayout(controls_host)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        self.title = TitleLabel(tr("Translate", lang))
        layout.addWidget(self.title)

        # --- Colorful format-category header ---
        cat_host = QWidget()
        cat_flow = FlowLayout(cat_host, needAni=False)
        cat_flow.setHorizontalSpacing(12)
        cat_flow.setVerticalSpacing(12)
        for key, fmts, color, icon in _FORMAT_CATEGORIES:
            card = FormatCategoryCard(tr(key, lang), fmts, color, icon)
            card._lh_key = key
            self._fmt_cards.append(card)
            cat_flow.addWidget(card)
        layout.addWidget(cat_host)

        # --- File picker ---
        file_row = QHBoxLayout()
        self.pick_btn = PushButton(FluentIcon.DOCUMENT, tr("Upload Files", lang))
        self.pick_btn.clicked.connect(self.on_pick_files)
        file_row.addWidget(self.pick_btn)
        self.files_label = BodyLabel(tr("Please select file(s) to translate.", lang))
        file_row.addWidget(self.files_label, 1)
        layout.addLayout(file_row)
        self.accepted_label = CaptionLabel(
            "Accepted: " + " ".join(backend.accepted_extensions()))
        layout.addWidget(self.accepted_label)

        # --- Languages with swap ---
        lang_row = QHBoxLayout()
        self.src_combo = ComboBox()
        self.dst_combo = ComboBox()
        langs = backend.available_languages()
        self.src_combo.addItems(langs)
        self.dst_combo.addItems(langs)
        config = backend.read_config()
        self._set_combo(self.src_combo, config.get("default_src_lang", "English"))
        self._set_combo(self.dst_combo, config.get("default_dst_lang", "English"))
        self.src_combo.currentTextChanged.connect(
            lambda v: backend.set_config("default_src_lang", v))
        self.dst_combo.currentTextChanged.connect(
            lambda v: backend.set_config("default_dst_lang", v))
        self.swap_btn = ToolButton(FluentIcon.ROTATE)
        self.swap_btn.clicked.connect(self.on_swap)
        self.from_label = BodyLabel(tr("Source Language", lang))
        self.to_label = BodyLabel(tr("Target Language", lang))
        lang_row.addWidget(self.from_label)
        lang_row.addWidget(self.src_combo, 1)
        lang_row.addWidget(self.swap_btn)
        lang_row.addWidget(self.to_label)
        lang_row.addWidget(self.dst_combo, 1)
        layout.addLayout(lang_row)

        # --- Model card ---
        model_card = CardWidget()
        model_form = QFormLayout(model_card)
        model_form.setContentsMargins(20, 14, 20, 14)
        model_form.setSpacing(10)

        online_row = QHBoxLayout()
        self.online_switch = SwitchButton()
        self.online_switch.setChecked(config.get("default_online", False))
        self.online_switch.checkedChanged.connect(self.on_online_toggle)
        online_row.addWidget(self.online_switch)
        online_row.addStretch(1)
        self.online_label = BodyLabel(tr("Use Online Model", lang))
        model_form.addRow(self.online_label, online_row)

        model_row = QHBoxLayout()
        self.model_combo = ComboBox()
        self.model_combo.setMinimumWidth(160)
        model_row.addWidget(self.model_combo, 1)
        self.refresh_models_btn = ToolButton(FluentIcon.SYNC)
        self.refresh_models_btn.clicked.connect(self.on_refresh_models)
        model_row.addWidget(self.refresh_models_btn)
        self.model_label = BodyLabel(tr("Models", lang))
        model_form.addRow(self.model_label, model_row)

        self.api_key_edit = PasswordLineEdit()
        self.api_key_edit.setPlaceholderText(tr("Enter your API key here", lang))
        self.api_key_label = BodyLabel(tr("API Key", lang))
        model_form.addRow(self.api_key_label, self.api_key_edit)

        self.glossary_combo = ComboBox()
        self.glossary_combo.addItems(backend.get_glossary_files())
        self.glossary_label = BodyLabel(tr("Glossary", lang))
        model_form.addRow(self.glossary_label, self.glossary_combo)

        layout.addWidget(model_card)

        # --- Contextual bilingual switches ---
        self.bilingual_card = CardWidget()
        self.bilingual_layout = QVBoxLayout(self.bilingual_card)
        self.bilingual_layout.setContentsMargins(20, 10, 20, 10)
        self.bilingual_card.setVisible(False)
        layout.addWidget(self.bilingual_card)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        self.translate_btn = PrimaryPushButton(FluentIcon.SEND, tr("Translate", lang))
        self.translate_btn.clicked.connect(self.on_translate)
        self.stop_btn = PushButton(FluentIcon.CANCEL, tr("Stop Translation", lang))
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        action_row.addWidget(self.translate_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        # --- Inline progress + status (kept for quick feedback) ---
        self.progress = ProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = BodyLabel("")
        layout.addWidget(self.status_label)

        # --- Result ---
        result_row = QHBoxLayout()
        self.open_output_btn = PushButton(FluentIcon.FOLDER, tr("Open Output Folder", lang))
        self.open_output_btn.setEnabled(False)
        self.open_output_btn.clicked.connect(
            lambda: open_folder(self._last_output_dir))
        result_row.addWidget(self.open_output_btn)
        result_row.addStretch(1)
        layout.addLayout(result_row)

        layout.addStretch(1)

        # --- dashboard view ---
        self.dashboard = ProgressDashboard(lang=lang, on_stop=self.on_stop)

        self.addWidget(self._controls)
        self.addWidget(self.dashboard)
        self.setCurrentWidget(self._controls)

        self.refresh_model_list()
        self._update_api_key_visibility()

    # --- i18n ---
    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Translate", lang))
        for card in self._fmt_cards:
            card.set_title(tr(card._lh_key, lang))
        self.pick_btn.setText(tr("Upload Files", lang))
        if not self._files:
            self.files_label.setText(tr("Please select file(s) to translate.", lang))
        self.from_label.setText(tr("Source Language", lang))
        self.to_label.setText(tr("Target Language", lang))
        self.online_label.setText(tr("Use Online Model", lang))
        self.model_label.setText(tr("Models", lang))
        self.api_key_label.setText(tr("API Key", lang))
        self.api_key_edit.setPlaceholderText(tr("Enter your API key here", lang))
        self.glossary_label.setText(tr("Glossary", lang))
        self.translate_btn.setText(tr("Translate", lang))
        self.stop_btn.setText(tr("Stop Translation", lang))
        self.open_output_btn.setText(tr("Open Output Folder", lang))
        self.dashboard.retranslate(lang)

    # --- helpers ---
    @staticmethod
    def _set_combo(combo, value):
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def refresh_glossaries(self):
        current = self.glossary_combo.currentText()
        self.glossary_combo.clear()
        names = backend.get_glossary_files()
        self.glossary_combo.addItems(names)
        self._set_combo(self.glossary_combo, current)

    def refresh_model_list(self):
        use_online = self.online_switch.isChecked()
        current = self.model_combo.currentText()
        self.model_combo.clear()
        if use_online:
            models = backend.scan_online_models()
        else:
            models = backend.scan_local_models()
        if not models:
            models = ["(no models found)"]
        self.model_combo.addItems(models)
        # Prefer the active interface persisted by the Interface page.
        active = backend.get_active_model(use_online)
        self._set_combo(self.model_combo, active or current)

    def refresh_active_interface(self):
        """Called by MainWindow when the Interface page changes the active model:
        align the online switch + model selection to the persisted active one."""
        online = backend.get_config("default_online", False)
        self.online_switch.blockSignals(True)
        self.online_switch.setChecked(online)
        self.online_switch.blockSignals(False)
        self._update_api_key_visibility()
        self.refresh_model_list()

    def _update_api_key_visibility(self):
        show = self.online_switch.isChecked()
        self.api_key_edit.setVisible(show)
        self.api_key_label.setVisible(show)

    # --- handlers ---
    def on_pick_files(self):
        exts = backend.accepted_extensions()
        filt = "Supported files (" + " ".join(f"*{e}" for e in exts) + ");;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Upload Files", self._lang), "", filt)
        if not paths:
            return
        self._files = paths
        names = ", ".join(os.path.basename(p) for p in paths)
        self.files_label.setText(names if len(names) < 80 else f"{len(paths)} files selected")
        self._rebuild_bilingual_switches()

    def _rebuild_bilingual_switches(self):
        # clear existing
        while self.bilingual_layout.count():
            item = self.bilingual_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._bilingual_switches.clear()

        keys = backend.bilingual_keys_for_files(self._files)
        config = backend.read_config()
        for key in keys:
            row = QHBoxLayout()
            sw = SwitchButton()
            sw.setChecked(config.get(key, False))
            label_key = key
            sw.checkedChanged.connect(
                lambda v, k=label_key: backend.set_config(k, v))
            row.addWidget(BodyLabel(tr(backend.BILINGUAL_LABEL.get(key, key), self._lang)))
            row.addStretch(1)
            row.addWidget(sw)
            container = QWidget()
            container.setLayout(row)
            self.bilingual_layout.addWidget(container)
            self._bilingual_switches[key] = sw
        self.bilingual_card.setVisible(bool(keys))

    def on_swap(self):
        s, d = self.src_combo.currentText(), self.dst_combo.currentText()
        self._set_combo(self.src_combo, d)
        self._set_combo(self.dst_combo, s)

    def on_online_toggle(self, value):
        backend.set_config("default_online", value)
        self._update_api_key_visibility()
        self.refresh_model_list()

    def on_refresh_models(self):
        if self.online_switch.isChecked():
            models, status = backend.fetch_online_models(
                self.model_combo.currentText(), self.api_key_edit.text())
            current = self.model_combo.currentText()
            self.model_combo.clear()
            self.model_combo.addItems(models or ["(no models found)"])
            self._set_combo(self.model_combo, current)
            self._info(tr("Models", self._lang), status)
        else:
            backend.scan_local_models(force_refresh=True)
            self.refresh_model_list()
            self._info(tr("Models", self._lang), "Local model list refreshed.")

    def on_translate(self):
        if self._running:
            return
        if not self._files:
            self._info(tr("Translate", self._lang),
                       tr("Please select file(s) to translate.", self._lang), error=True)
            return
        model = self.model_combo.currentText()
        if not model or model == "(no models found)":
            self._info(tr("Translate", self._lang),
                       tr("Please select a model first", self._lang), error=True)
            return
        use_online = self.online_switch.isChecked()
        api_key = self.api_key_edit.text()
        if use_online and not api_key:
            self._info(tr("Translate", self._lang),
                       tr("API key is required for online models.", self._lang), error=True)
            return

        # Detect base-name collisions; only those files need isolation subdirs.
        bases = [os.path.splitext(os.path.basename(p))[0] for p in self._files]
        self._needs_isolation = len(set(bases)) != len(bases)
        self._run_subdir = ("run_" + uuid.uuid4().hex[:8]) if self._needs_isolation else None

        self._queue = list(self._files)
        self._total = len(self._files)
        self._results = []
        self._file_results = []
        self._workers = []
        self._progress = {}
        self._tokens = 0
        self._running = True
        self.translate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.open_output_btn.setEnabled(False)
        self.progress.setValue(0)

        # Switch to the metric dashboard for the duration of the run.
        self.dashboard.start()
        self._refresh_dashboard()
        self.setCurrentWidget(self.dashboard)

        pool = min(self._total, backend.MAX_CONCURRENT_TASKS)
        for _ in range(pool):
            self._start_next()

    def _start_next(self):
        if not self._queue:
            return
        file_path = self._queue.pop(0)
        config = backend.read_config()
        use_online = self.online_switch.isChecked()
        flags = {k: sw.isChecked() for k, sw in self._bilingual_switches.items()}
        # Isolate by a per-file subdir only when base names collide.
        isolation = None
        if self._needs_isolation:
            isolation = os.path.join(self._run_subdir, uuid.uuid4().hex[:6])
        worker = TranslationWorker(
            file_path=file_path,
            model=self.model_combo.currentText(),
            use_online=use_online,
            api_key=self.api_key_edit.text(),
            src_lang=self.src_combo.currentText(),
            dst_lang=self.dst_combo.currentText(),
            max_token=config.get("max_token", 768),
            max_retries=config.get("max_retries", 4),
            thread_count=backend.thread_count_for_mode(use_online),
            glossary_name=self.glossary_combo.currentText(),
            bilingual_flags=flags,
            session_lang=self._lang,
            isolation_subdir=isolation,
        )
        worker._lh_file = file_path
        worker.progress.connect(lambda v, d, w=worker: self.on_progress(w, v, d))
        worker.finished.connect(lambda p, m, w=worker: self.on_file_finished(w, p, m))
        worker.failed.connect(lambda msg, w=worker: self.on_file_failed(w, msg))
        self._workers.append(worker)
        self._progress[worker] = 0.0
        worker.start()
        self.status_label.setText(
            tr("Translating", self._lang) + f" {os.path.basename(file_path)}...")
        self._refresh_dashboard()

    def _aggregate_progress(self):
        # Finished files count as 1.0; in-flight files use their last fraction.
        finished = self._total - len(self._workers) - len(self._queue)
        running_frac = sum(self._progress.values())
        total_frac = finished + running_frac
        return int((total_frac / self._total) * 100) if self._total else 0

    def _done_count(self):
        return len([r for r in self._file_results if r[1] == "ok"])

    def _failed_count(self):
        return len([r for r in self._file_results if r[1] == "failed"])

    def _refresh_dashboard(self):
        self.dashboard.update_metrics(
            percent=self._aggregate_progress(),
            total_files=self._total,
            done_files=self._done_count(),
            live_tasks=len(self._workers),
            failed=self._failed_count(),
            total_tokens=self._tokens,
        )

    def on_progress(self, worker, value, desc):
        self._progress[worker] = float(value)
        self.progress.setValue(self._aggregate_progress())
        if desc:
            name = os.path.basename(getattr(worker, "_lh_file", ""))
            self.status_label.setText(f"{name}: {desc}" if name else desc)
            # Opportunistically scrape a token total from the final desc.
            self._tokens = max(self._tokens, _parse_tokens(desc))
        self._refresh_dashboard()

    def _retire(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)
        self._progress.pop(worker, None)
        worker.wait(2000)
        # Launch the next queued file to keep the pool full.
        if self._queue and self._running:
            self._start_next()
        self._refresh_dashboard()
        if not self._workers and not self._queue:
            self._finish_all()

    def on_file_finished(self, worker, output_path, missing):
        name = os.path.basename(getattr(worker, "_lh_file", output_path))
        self._results.append(output_path)
        self._last_output_dir = os.path.dirname(output_path)
        self.open_output_btn.setEnabled(True)
        detail = ""
        if missing:
            tmpl = tr("Missing Segments", self._lang)
            detail = tmpl.format(count=len(missing)) if "{count}" in tmpl else f"{len(missing)} missing"
        self._file_results.append((name, "ok", detail))
        self._retire(worker)

    def on_file_failed(self, worker, message):
        name = os.path.basename(getattr(worker, "_lh_file", "?"))
        self._file_results.append((name, "failed", message))
        self.status_label.setText(f"{name}: {message}")
        self._retire(worker)

    def on_stop(self):
        self.status_label.setText(tr("Stopping", self._lang) + "...")
        for worker in list(self._workers):
            if worker.isRunning():
                worker.request_stop()
        # Drop anything not yet started so the pool drains.
        self._queue = []

    def _finish_all(self):
        self._running = False
        self.translate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        ok = [r for r in self._file_results if r[1] == "ok"]
        failed = [r for r in self._file_results if r[1] == "failed"]
        if ok:
            self.progress.setValue(100)
        # For multi-file runs, package a zip with a per-file results.txt.
        if len(self._file_results) > 1 and self._results:
            try:
                zip_path = backend.zip_results(self._results, self._file_results)
                self._last_output_dir = os.path.dirname(zip_path)
                self.open_output_btn.setEnabled(True)
            except Exception:  # noqa: BLE001 - zipping is best-effort
                pass
        summary = f"{tr('Completed Files', self._lang)}: {len(ok)}"
        if failed:
            summary += f" | {tr('Failed', self._lang)}: {len(failed)}"
        self.status_label.setText(summary)
        self._refresh_dashboard()
        # Return to the controls view so the user can start another run.
        self.setCurrentWidget(self._controls)
        self._info(tr("Translate", self._lang), summary, error=bool(failed and not ok))

    def _info(self, title, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)


def _parse_tokens(desc):
    """Best-effort extraction of a token count from a status string like
    '... Total tokens used: 12.3K' or '... 4500 tokens'."""
    import re
    m = re.search(r"([\d.]+)\s*([KkMm]?)\s*(?:tokens|tokens used)", desc)
    if not m:
        m = re.search(r"tokens?(?:\s*used)?\s*[:=]?\s*([\d.]+)\s*([KkMm]?)", desc)
    if not m:
        return 0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0
    unit = m.group(2).lower()
    if unit == "k":
        val *= 1000
    elif unit == "m":
        val *= 1_000_000
    return int(val)
