"""Translate page: pick files, choose languages/model/glossary, translate.

Translations run one file at a time on a TranslationWorker (QThread). Bilingual
toggles appear contextually for the uploaded file types. On success an InfoBar
shows and an "open output folder" button is enabled.
"""

import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFormLayout,
)

from qfluentwidgets import (
    ComboBox, PushButton, PrimaryPushButton, ProgressBar, LineEdit,
    SwitchButton, BodyLabel, StrongBodyLabel, CaptionLabel, CardWidget,
    InfoBar, InfoBarPosition, FluentIcon, ToolButton, PasswordLineEdit,
)

from qt_app import backend
from qt_app.worker import TranslationWorker
from qt_app.history_page import open_folder


class TranslatePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TranslatePage")
        self._files = []
        self._worker = None
        self._last_output_dir = None
        self._bilingual_switches = {}  # config-key -> SwitchButton

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        layout.addWidget(StrongBodyLabel("Translate"))

        # --- File picker ---
        file_row = QHBoxLayout()
        self.pick_btn = PushButton(FluentIcon.DOCUMENT, "Choose Files")
        self.pick_btn.clicked.connect(self.on_pick_files)
        file_row.addWidget(self.pick_btn)
        self.files_label = BodyLabel("No files selected")
        file_row.addWidget(self.files_label, 1)
        layout.addLayout(file_row)
        layout.addWidget(CaptionLabel(
            "Accepted: " + " ".join(backend.accepted_extensions())))

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
        lang_row.addWidget(BodyLabel("From:"))
        lang_row.addWidget(self.src_combo, 1)
        lang_row.addWidget(self.swap_btn)
        lang_row.addWidget(BodyLabel("To:"))
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
        model_form.addRow(BodyLabel("Use online model"), online_row)

        model_row = QHBoxLayout()
        self.model_combo = ComboBox()
        self.model_combo.setMinimumWidth(240)
        model_row.addWidget(self.model_combo, 1)
        self.refresh_models_btn = ToolButton(FluentIcon.SYNC)
        self.refresh_models_btn.clicked.connect(self.on_refresh_models)
        model_row.addWidget(self.refresh_models_btn)
        model_form.addRow(BodyLabel("Model"), model_row)

        self.api_key_edit = PasswordLineEdit()
        self.api_key_edit.setPlaceholderText("Enter your API key here")
        self.api_key_label = BodyLabel("API Key")
        model_form.addRow(self.api_key_label, self.api_key_edit)

        self.glossary_combo = ComboBox()
        self.glossary_combo.addItems(backend.get_glossary_files())
        model_form.addRow(BodyLabel("Glossary"), self.glossary_combo)

        layout.addWidget(model_card)

        # --- Contextual bilingual switches ---
        self.bilingual_card = CardWidget()
        self.bilingual_layout = QVBoxLayout(self.bilingual_card)
        self.bilingual_layout.setContentsMargins(20, 10, 20, 10)
        self.bilingual_card.setVisible(False)
        layout.addWidget(self.bilingual_card)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        self.translate_btn = PrimaryPushButton(FluentIcon.SEND, "Translate")
        self.translate_btn.clicked.connect(self.on_translate)
        self.stop_btn = PushButton(FluentIcon.CANCEL, "Stop")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        action_row.addWidget(self.translate_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        # --- Progress + status ---
        self.progress = ProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = BodyLabel("")
        layout.addWidget(self.status_label)

        # --- Result ---
        result_row = QHBoxLayout()
        self.open_output_btn = PushButton(FluentIcon.FOLDER, "Open Output Folder")
        self.open_output_btn.setEnabled(False)
        self.open_output_btn.clicked.connect(
            lambda: open_folder(self._last_output_dir))
        result_row.addWidget(self.open_output_btn)
        result_row.addStretch(1)
        layout.addLayout(result_row)

        layout.addStretch(1)

        self.refresh_model_list()
        self._update_api_key_visibility()

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
        self._set_combo(self.model_combo, current)

    def _update_api_key_visibility(self):
        show = self.online_switch.isChecked()
        self.api_key_edit.setVisible(show)
        self.api_key_label.setVisible(show)

    # --- handlers ---
    def on_pick_files(self):
        exts = backend.accepted_extensions()
        filt = "Supported files (" + " ".join(f"*{e}" for e in exts) + ");;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files", "", filt)
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
            row.addWidget(BodyLabel(backend.BILINGUAL_LABEL.get(key, key)))
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
            self._info("Models", status)
        else:
            backend.scan_local_models(force_refresh=True)
            self.refresh_model_list()
            self._info("Models", "Local model list refreshed.")

    def on_translate(self):
        if not self._files:
            self._info("Translate", "Please choose file(s) first.", error=True)
            return
        model = self.model_combo.currentText()
        if not model or model == "(no models found)":
            self._info("Translate", "Please select a model.", error=True)
            return
        use_online = self.online_switch.isChecked()
        api_key = self.api_key_edit.text()
        if use_online and not api_key:
            self._info("Translate", "API key is required for online models.", error=True)
            return

        # one file at a time; queue the rest
        self._queue = list(self._files)
        self._results = []
        self.translate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._start_next()

    def _start_next(self):
        if not self._queue:
            self._finish_all()
            return
        file_path = self._queue.pop(0)
        config = backend.read_config()
        use_online = self.online_switch.isChecked()
        flags = {k: sw.isChecked() for k, sw in self._bilingual_switches.items()}
        self._worker = TranslationWorker(
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
        )
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_file_finished)
        self._worker.failed.connect(self.on_file_failed)
        self.status_label.setText(f"Translating {os.path.basename(file_path)}...")
        self._worker.start()

    def on_progress(self, value, desc):
        self.progress.setValue(int(value * 100))
        if desc:
            self.status_label.setText(desc)

    def on_file_finished(self, output_path, missing):
        self._results.append(output_path)
        self._last_output_dir = os.path.dirname(output_path)
        self.open_output_btn.setEnabled(True)
        if missing:
            self._info("Translate",
                       f"Done with {len(missing)} missing segment(s): {os.path.basename(output_path)}")
        self._start_next()

    def on_file_failed(self, message):
        self.translate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText(message)
        self._info("Translate", message, error=True)

    def on_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self.status_label.setText("Stopping...")

    def _finish_all(self):
        self.translate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self._results:
            self.progress.setValue(100)
            self.status_label.setText(f"Completed {len(self._results)} file(s).")
            self._info("Translate", f"Completed {len(self._results)} file(s).")

    def _info(self, title, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)
