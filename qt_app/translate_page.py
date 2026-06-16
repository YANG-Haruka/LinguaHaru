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
    ComboBox, PushButton, PrimaryPushButton, SwitchButton,
    BodyLabel, CardWidget, TitleLabel, StrongBodyLabel, CaptionLabel, LineEdit,
    InfoBar, InfoBarPosition, FluentIcon, ToolButton,
    ScrollArea,
)

from core import backend
from qt_app.i18n import tr
from qt_app.worker import TranslationWorker
from qt_app.history_page import open_folder
from qt_app.widgets import DropZone
from qt_app.progress_dashboard import ProgressDashboard
from core.api_keys import load_api_key_for_model
from core.languages_config import LANGUAGE_MAP
from core.pipelines.video_translation_pipeline import (
    STT_MODELS, get_selected_stt_model, get_stt_model, SENSEVOICE_SUPPORTED_CODES)
from core.optional_modules import MEDIA_EXTENSIONS


class TranslatePage(QStackedWidget):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("TranslatePage")
        self.setAcceptDrops(True)  # drag files anywhere onto the page
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
        self._coverage = []         # per-file coverage reports
        self._run_subdir = None
        self._running = False
        self._total = 0
        self._tokens = 0
        self._fmt_cards = []
        # Set by MainWindow: jump to the Plugins page when an unavailable
        # format card is clicked, and to the Interface page from the button.
        self.on_open_plugins = None
        self.on_open_interface = None

        # --- controls view (scrollable) ---
        self._controls = ScrollArea()
        self._controls.setWidgetResizable(True)
        # Show a horizontal scrollbar only if truly needed (never CLIP content
        # at the right edge, which is what AlwaysOff did).
        self._controls.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._controls.enableTransparentBackground()
        controls_host = QWidget()
        controls_host.setObjectName("translateControlsHost")
        # The scroll viewport is transparent, but its inner widget paints with
        # the default (system) palette -> dark on a Windows dark desktop. Make
        # it transparent too so the themed window surface shows through.
        controls_host.setStyleSheet("#translateControlsHost { background-color: transparent; }")
        self._controls.setWidget(controls_host)
        layout = QVBoxLayout(controls_host)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        self.title = TitleLabel(tr("File Translation", lang))
        layout.addWidget(self.title)

        # --- File picker: the one big click-or-drop module. The format
        # categories (Books / Documents / Subtitles / ...) drift across its
        # background as a marquee, so no separate card row is needed. ---
        self.dropzone = DropZone(tr("Drop files to upload", lang))
        self.dropzone.setMinimumHeight(190)
        self.dropzone.clicked.connect(self.on_pick_files)
        self.dropzone.filesDropped.connect(self._on_files_dropped)
        layout.addWidget(self.dropzone)
        self._refresh_format_availability()

        # --- Languages with swap ---
        lang_row = QHBoxLayout()
        self.src_combo = ComboBox()
        self.dst_combo = ComboBox()
        langs = backend.available_languages()
        # Source supports auto-detection ("Auto"); target is always concrete.
        self.src_combo.addItems(["Auto"] + langs)
        self.dst_combo.addItems(langs)
        config = backend.read_config()
        self._set_combo(self.src_combo, config.get("default_src_lang", "Auto"))
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

        # --- Active interface (chosen in Interface Management) + glossary ---
        model_card = CardWidget()
        model_form = QFormLayout(model_card)
        model_form.setContentsMargins(20, 14, 20, 14)
        model_form.setSpacing(10)

        iface_row = QHBoxLayout()
        self.active_interface_label = BodyLabel("-")
        iface_row.addWidget(self.active_interface_label, 1)
        self.iface_btn = PushButton(FluentIcon.CONNECT, tr("Interface Management", lang))
        self.iface_btn.clicked.connect(lambda: self.on_open_interface() if callable(self.on_open_interface) else None)
        iface_row.addWidget(self.iface_btn)
        self.iface_field_label = BodyLabel(tr("Current Interface", lang))
        model_form.addRow(self.iface_field_label, iface_row)

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

        # --- Contextual media (video/audio) STT options ---
        self.media_card = CardWidget()
        media_form = QFormLayout(self.media_card)
        media_form.setContentsMargins(20, 14, 20, 14)
        media_form.setSpacing(10)
        self._stt_ids = []
        self.stt_combo = ComboBox()
        self.stt_combo.currentIndexChanged.connect(self._on_stt_changed)
        self.stt_label = BodyLabel(tr("Speech-to-Text Model", lang))
        media_form.addRow(self.stt_label, self.stt_combo)
        self.stt_empty_hint = CaptionLabel(tr("No STT downloaded", lang))
        self.stt_empty_hint.setWordWrap(True)
        self.stt_empty_hint.setVisible(False)
        media_form.addRow("", self.stt_empty_hint)
        self._refresh_stt_models()
        sub_row = QHBoxLayout()
        self.translate_subs_switch = SwitchButton()
        self.translate_subs_switch.setChecked(config.get("translate_subtitles", True))
        self.translate_subs_switch.checkedChanged.connect(
            lambda v: backend.set_config("translate_subtitles", v))
        sub_row.addWidget(self.translate_subs_switch)
        sub_row.addStretch(1)
        self.translate_subs_label = BodyLabel(tr("Translate Subtitles", lang))
        media_form.addRow(self.translate_subs_label, sub_row)
        self.media_card.setVisible(False)
        layout.addWidget(self.media_card)

        # --- Contextual PDF options (shown only when a PDF is selected) ---
        self.pdf_card = self._build_pdf_card(lang, config)
        self.pdf_card.setVisible(False)
        layout.addWidget(self.pdf_card)

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

        # Progress, status and the open-output button live ONLY on the detailed
        # dashboard now (not cluttering the controls page).
        layout.addStretch(1)

        # --- dashboard view ---
        self.dashboard = ProgressDashboard(
            lang=lang, on_stop=self.on_stop,
            on_open=lambda: open_folder(self._last_output_dir),
            on_back=self._back_to_controls)

        self.addWidget(self._controls)
        self.addWidget(self.dashboard)
        self.setCurrentWidget(self._controls)

        self.refresh_active_interface()

    # --- i18n ---
    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("File Translation", lang))
        if self._files:
            self.dropzone.set_prompt(self._file_summary(self._files))
        else:
            self.dropzone.set_prompt(tr("Drop files to upload", lang))
        self.from_label.setText(tr("Source Language", lang))
        self.to_label.setText(tr("Target Language", lang))
        self.iface_field_label.setText(tr("Current Interface", lang))
        self.iface_btn.setText(tr("Interface Management", lang))
        self.refresh_active_interface()
        self.glossary_label.setText(tr("Glossary", lang))
        self.stt_label.setText(tr("Speech-to-Text Model", lang))
        self.translate_subs_label.setText(tr("Translate Subtitles", lang))
        self.pdf_title.setText(tr("PDF Options", lang))
        for label, label_key, caption, hint_key in self._pdf_label_specs:
            label.setText(tr(label_key, lang))
            caption.setText(tr(hint_key, lang))
        self.pdf_pages_label.setText(tr("Page Range", lang))
        self.pdf_pages_caption.setText(tr("Page Range Hint", lang))
        self.translate_btn.setText(tr("Translate", lang))
        self.stop_btn.setText(tr("Stop Translation", lang))
        self.dashboard.retranslate(lang)

    def _refresh_format_availability(self):
        """No-op: the colourful card row was removed; categories now live in the
        drop-zone background marquee. Kept so page-change / theme / retranslate
        callers don't need to special-case it."""
        pass

    def _build_pdf_card(self, lang, config):
        """PDF-specific options, surfaced only when a PDF is in the selection.
        Each control reads its initial value from config and writes back to the
        backend on change; labels/captions are tracked for retranslate()."""
        card = CardWidget()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(10)

        self.pdf_title = StrongBodyLabel(tr("PDF Options", lang))
        v.addWidget(self.pdf_title)

        # (label_key, hint_key, config_key, attr) for the boolean switches.
        switch_specs = [
            ("Translate Tables", "Translate Tables Hint",
             "pdf_translate_table", "pdf_table_switch"),
            ("Scanned PDF OCR", "Scanned PDF OCR Hint",
             "pdf_ocr_scanned", "pdf_ocr_switch"),
            ("Dual Alternating Pages", "Dual Alternating Hint",
             "pdf_dual_alternating", "pdf_dual_switch"),
            ("Only Translated Pages", "Only Translated Pages Hint",
             "pdf_only_translated_pages", "pdf_only_switch"),
        ]
        # Track (label_widget, label_key, caption_widget, hint_key) for retranslate.
        self._pdf_label_specs = []

        for label_key, hint_key, cfg_key, attr in switch_specs:
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            label = BodyLabel(tr(label_key, lang))
            caption = CaptionLabel(tr(hint_key, lang))
            caption.setTextColor("#606060", "#a0a0a0")
            col.addWidget(label)
            col.addWidget(caption)
            row.addLayout(col, 1)
            sw = SwitchButton()
            sw.setChecked(config.get(cfg_key, False))
            sw.checkedChanged.connect(
                lambda val, k=cfg_key: backend.set_config(k, val))
            setattr(self, attr, sw)
            row.addWidget(sw)
            v.addLayout(row)
            self._pdf_label_specs.append((label, label_key, caption, hint_key))

        # Page range (LineEdit), saved on textChanged.
        pr_col = QVBoxLayout()
        pr_col.setSpacing(2)
        self.pdf_pages_label = BodyLabel(tr("Page Range", lang))
        self.pdf_pages_caption = CaptionLabel(tr("Page Range Hint", lang))
        self.pdf_pages_caption.setTextColor("#606060", "#a0a0a0")
        self.pdf_pages_edit = LineEdit()
        self.pdf_pages_edit.setPlaceholderText("1-3,5")
        self.pdf_pages_edit.setText(str(config.get("pdf_pages", "")))
        self.pdf_pages_edit.textChanged.connect(
            lambda val: backend.set_config("pdf_pages", val))
        pr_col.addWidget(self.pdf_pages_label)
        pr_col.addWidget(self.pdf_pages_caption)
        pr_col.addWidget(self.pdf_pages_edit)
        v.addLayout(pr_col)

        return card

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

    def _active(self):
        """(use_online, model, api_key) for the interface chosen in 接口管理."""
        online = backend.get_config("default_online", True)
        model = backend.get_active_model(online)
        api_key = load_api_key_for_model(model) if online else ""
        return online, model, api_key

    def refresh_active_interface(self):
        """Reflect the active interface (set in Interface Management) in the
        read-only label, so the user sees what will be used."""
        online, model, _ = self._active()
        if model:
            tag = tr("Use Online Model", self._lang) if online else tr("Local", self._lang)
            self.active_interface_label.setText(f"{model}  ·  {tag}")
        else:
            self.active_interface_label.setText(tr("No active interface", self._lang))

    # --- handlers ---
    def on_pick_files(self):
        exts = backend.accepted_extensions()
        filt = "Supported files (" + " ".join(f"*{e}" for e in exts) + ");;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Upload Files", self._lang), "", filt)
        if paths:
            self._set_files(paths)

    def _on_files_dropped(self, paths):
        """Files dropped onto the drop zone: keep only accepted extensions."""
        accepted = set(backend.accepted_extensions())
        paths = [p for p in paths if os.path.splitext(p)[1].lower() in accepted]
        if paths:
            self._set_files(paths)
        else:
            self._info(tr("Translate", self._lang),
                       tr("Please select file(s) to translate.", self._lang), error=True)

    def _file_summary(self, paths):
        """One-line summary of the selection, like the Web dropzone text."""
        if len(paths) == 1:
            try:
                mb = os.path.getsize(paths[0]) / 1048576
            except OSError:
                mb = 0
            return f"{os.path.basename(paths[0])}  ({mb:.1f} MB)"
        tmpl = tr("Files selected count", self._lang)
        head = tmpl.format(count=len(paths)) if "{count}" in tmpl else f"{len(paths)} files: "
        names = "、".join(os.path.basename(p) for p in paths)
        s = head + names
        return s if len(s) <= 80 else s[:79] + "…"

    def _set_files(self, paths):
        self._files = paths
        self.dropzone.set_prompt(self._file_summary(paths))
        self._rebuild_bilingual_switches()
        # Show STT options only for media files; apply SenseVoice lang limits.
        has_media = any(os.path.splitext(p)[1].lower() in MEDIA_EXTENSIONS for p in paths)
        if has_media:
            self._refresh_stt_models()   # reflect models installed since last view
        self.media_card.setVisible(has_media)
        if has_media and 0 <= self.stt_combo.currentIndex() < len(self._stt_ids):
            self._apply_stt_language_restriction(self._stt_ids[self.stt_combo.currentIndex()])
        # Show PDF options only when the selection includes a PDF.
        has_pdf = any(os.path.splitext(p)[1].lower() == ".pdf" for p in paths)
        self.pdf_card.setVisible(has_pdf)

    # --- drag & drop ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        accepted = set(backend.accepted_extensions())
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.splitext(p)[1].lower() in accepted]
        if paths:
            self._set_files(paths)
        else:
            self._info(tr("Translate", self._lang),
                       tr("Please select file(s) to translate.", self._lang), error=True)

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

    def _refresh_stt_models(self):
        """Offer only DOWNLOADED STT models — selection happens here at translate
        time; models are installed in Settings -> Model Management."""
        from core.optional_modules import plugin_model_states
        states = [s for s in plugin_model_states("Video/Audio") if s["downloaded"]]
        self._stt_ids = [s["id"] for s in states]
        self.stt_combo.blockSignals(True)
        self.stt_combo.clear()
        self.stt_combo.addItems([s["label"] for s in states])
        sel = get_selected_stt_model()
        if sel in self._stt_ids:
            self.stt_combo.setCurrentIndex(self._stt_ids.index(sel))
        elif self._stt_ids:
            self.stt_combo.setCurrentIndex(0)
        self.stt_combo.blockSignals(False)
        self.stt_combo.setVisible(bool(self._stt_ids))
        self.stt_label.setVisible(bool(self._stt_ids))
        self.stt_empty_hint.setVisible(not self._stt_ids)

    def _on_stt_changed(self, index):
        if not (0 <= index < len(self._stt_ids)):
            return
        stt_id = self._stt_ids[index]
        backend.set_config("stt_model", stt_id)
        self._apply_stt_language_restriction(stt_id)

    def _apply_stt_language_restriction(self, stt_id):
        """SenseVoice only handles zh/en/ja/ko/yue, so restrict the source
        language list to its supported set; other engines restore the full list.
        (Target is unaffected — the LLM handles translation.)"""
        full = backend.available_languages()
        if get_stt_model(stt_id)["engine"] == "sensevoice":
            allowed = [n for n in full if LANGUAGE_MAP.get(n) in SENSEVOICE_SUPPORTED_CODES]
        else:
            allowed = full
        # "Auto" (source auto-detect) is always offered, incl. for SenseVoice.
        allowed = ["Auto"] + allowed
        cur = self.src_combo.currentText()
        self.src_combo.blockSignals(True)
        self.src_combo.clear()
        self.src_combo.addItems(allowed)
        self._set_combo(self.src_combo, cur if cur in allowed else (allowed[0] if allowed else ""))
        self.src_combo.blockSignals(False)

    def on_swap(self):
        s, d = self.src_combo.currentText(), self.dst_combo.currentText()
        self._set_combo(self.src_combo, d)
        self._set_combo(self.dst_combo, s)

    def on_translate(self):
        if self._running:
            return
        if not self._files:
            self._info(tr("Translate", self._lang),
                       tr("Please select file(s) to translate.", self._lang), error=True)
            return
        use_online, model, api_key = self._active()
        if not model:
            self._info(tr("Translate", self._lang),
                       tr("Please select a model first", self._lang), error=True)
            if callable(self.on_open_interface):
                self.on_open_interface()
            return
        if use_online and not api_key:
            self._info(tr("Translate", self._lang),
                       tr("API key is required for online models.", self._lang), error=True)
            if callable(self.on_open_interface):
                self.on_open_interface()
            return

        # Detect base-name collisions; only those files need isolation subdirs.
        bases = [os.path.splitext(os.path.basename(p))[0] for p in self._files]
        self._needs_isolation = len(set(bases)) != len(bases)
        self._run_subdir = ("run_" + uuid.uuid4().hex[:8]) if self._needs_isolation else None

        self._queue = list(self._files)
        self._total = len(self._files)
        self._results = []
        self._file_results = []
        self._coverage = []         # per-file coverage reports
        self._workers = []
        self._progress = {}
        self._tokens = 0
        self._exact_tokens = 0      # summed exact usage for the thanks/cost card
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._run_model = ""
        self._run_online = False
        self._running = True
        self.translate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

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
        use_online, model, api_key = self._active()
        flags = {k: sw.isChecked() for k, sw in self._bilingual_switches.items()}
        # Isolate by a per-file subdir only when base names collide.
        isolation = None
        if self._needs_isolation:
            isolation = os.path.join(self._run_subdir, uuid.uuid4().hex[:6])
        worker = TranslationWorker(
            file_path=file_path,
            model=model,
            use_online=use_online,
            api_key=api_key,
            src_lang=self.src_combo.currentText(),
            dst_lang=self.dst_combo.currentText(),
            max_token=config.get("max_token", 768),
            max_retries=config.get("max_retries", 4),
            thread_count=backend.thread_count_for_mode(use_online, model),
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
        self.dashboard.set_status(
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
        if desc:
            name = os.path.basename(getattr(worker, "_lh_file", ""))
            line = f"{name}: {desc}" if name else desc
            self.dashboard.set_status(line)  # surface speed/ETA on the dashboard
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
        cov = getattr(worker, "coverage", None)
        if cov:
            self._coverage.append(cov)
        self._exact_tokens += getattr(worker, "total_tokens", 0) or 0
        self._prompt_tokens += getattr(worker, "prompt_tokens", 0) or 0
        self._completion_tokens += getattr(worker, "completion_tokens", 0) or 0
        self._run_model = getattr(worker, "model", "") or self._run_model
        self._run_online = getattr(worker, "use_online", False) or self._run_online
        detail = ""
        if missing:
            tmpl = tr("Missing Segments", self._lang)
            detail = tmpl.format(count=len(missing)) if "{count}" in tmpl else f"{len(missing)} missing"
        self._file_results.append((name, "ok", detail))
        self._retire(worker)

    def on_file_failed(self, worker, message):
        name = os.path.basename(getattr(worker, "_lh_file", "?"))
        self._file_results.append((name, "failed", message))
        self.dashboard.set_status(f"{name}: {message}")
        self._retire(worker)

    def on_stop(self):
        self.dashboard.set_status(tr("Stopping", self._lang) + "...")
        for worker in list(self._workers):
            if worker.isRunning():
                worker.request_stop()
        # Drop anything not yet started so the pool drains.
        self._queue = []

    def shutdown(self):
        """Stop in-flight translations and wait briefly, so document-translation
        worker threads aren't destroyed mid-run when the app closes."""
        self._queue = []
        self._running = False
        for worker in list(self._workers):
            try:
                if worker.isRunning():
                    worker.request_stop()
                    worker.wait(5000)
            except RuntimeError:
                pass

    def _back_to_controls(self):
        """Return from the (post-run) dashboard to the controls to start anew."""
        self.setCurrentWidget(self._controls)

    def _finish_all(self):
        self._running = False
        self.translate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        ok = [r for r in self._file_results if r[1] == "ok"]
        failed = [r for r in self._file_results if r[1] == "failed"]
        # For multi-file runs, package a zip with a per-file results.txt.
        if len(self._file_results) > 1 and self._results:
            try:
                zip_path = backend.zip_results(self._results, self._file_results)
                self._last_output_dir = os.path.dirname(zip_path)
            except Exception:  # noqa: BLE001 - zipping is best-effort
                pass
        done_tmpl = tr("Completed Files", self._lang)  # "完成 {done}/{total}"
        summary = done_tmpl.format(done=len(ok), total=len(self._file_results)) \
            if "{done}" in done_tmpl else f"{done_tmpl}: {len(ok)}"
        if failed:
            summary += f" | {tr('Failed', self._lang)}: {len(failed)}"
        self._refresh_dashboard()
        # Stay on the dashboard so the metrics (speed/tokens/time) remain visible;
        # just show a "done" banner + Open-folder / New-translation buttons.
        self.dashboard.show_done(summary, can_open=bool(self._results),
                                 coverage=self._aggregate_coverage())
        self._info(tr("Translate", self._lang), summary, error=bool(failed and not ok))
        # Thank-you + token/cost summary for the finished (long) translation run.
        if ok:
            cost_amount = cost_symbol = cost_currency = None
            if self._run_online:
                try:
                    from core.pricing import estimate_cost
                    amt, cost_symbol, cost_currency = estimate_cost(
                        self._run_model, self._prompt_tokens, self._completion_tokens, self._lang)
                    cost_amount = round(amt, 4)
                except Exception:  # noqa: BLE001
                    pass
            from qt_app.thanks import show_thanks
            show_thanks(self.window(), self._lang, self._exact_tokens,
                        cost_amount, cost_symbol, cost_currency)

    def _aggregate_coverage(self):
        """Merge per-file coverage reports into one (summed totals + categories).
        Returns None when no coverage was produced."""
        if not self._coverage:
            return None
        total = translated = fallback = 0
        by_category = {}
        for rep in self._coverage:
            total += rep.get("total", 0)
            translated += rep.get("translated", 0)
            fallback += rep.get("fallback", 0)
            for cat, n in (rep.get("by_category") or {}).items():
                by_category[cat] = by_category.get(cat, 0) + n
        return {"total": total, "translated": translated,
                "fallback": fallback, "by_category": by_category}

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
