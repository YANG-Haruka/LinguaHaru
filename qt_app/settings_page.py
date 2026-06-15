"""Settings page: every change is persisted to system_config.json immediately."""

import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QFileDialog

from qfluentwidgets import (
    ScrollArea, BodyLabel, StrongBodyLabel, SwitchButton, CaptionLabel,
    CardWidget, PushButton, LineEdit, FluentIcon, MessageBox,
)

from core import backend
from core import model_store
from qt_app.i18n import tr


class SettingsPage(ScrollArea):
    """Online-by-default, LAN mode, thread counts, retries, RPM limit, AI
    glossary extraction, and an Optional Modules status group."""

    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        self._lang = lang
        # Set by MainWindow so the language selector can drive a global retranslate.
        self.on_ui_lang_changed = None
        self.setWidgetResizable(True)
        # Without this, the scroll viewport paints the system palette (dark on a
        # Windows dark desktop) even in light mode -> "light nav, dark settings".
        self.enableTransparentBackground()

        container = QWidget()
        container.setObjectName("settingsScrollContainer")
        container.setStyleSheet(
            "#settingsScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(16)

        config = backend.read_config()

        self.section_translation = StrongBodyLabel(tr("Settings", lang))
        layout.addWidget(self.section_translation)

        # (Interface language now lives at the bottom of the nav rail.)
        # Per-model parameters (API key, RPM, thread count, max retries) and the
        # Google realtime-voice key now live in Interface Management — set them
        # by double-clicking a model there. Only truly-global options remain here.
        self.per_model_hint = BodyLabel(tr("Per Model Hint", lang))
        self.per_model_hint.setWordWrap(True)
        layout.addWidget(self.per_model_hint)

        # --- Translation options (not model-related): AI glossary + output dir ---
        # AI glossary extraction — its own card.
        self.section_options = StrongBodyLabel(tr("Translation Options", lang))
        layout.addWidget(self.section_options)
        glossary_card = CardWidget()
        gl_form = QFormLayout(glossary_card)
        gl_form.setContentsMargins(20, 16, 20, 16)
        gl_form.setSpacing(12)
        self.auto_glossary = SwitchButton()
        self.auto_glossary.setChecked(config.get("auto_extract_glossary", False))
        self.auto_glossary.checkedChanged.connect(
            lambda v: backend.set_config("auto_extract_glossary", v))
        self.auto_glossary_label = BodyLabel(tr("AI Glossary Extraction", lang))
        gl_form.addRow(self.auto_glossary_label, self.auto_glossary)
        layout.addWidget(glossary_card)

        # Output folder — separate card/section.
        self.section_output = StrongBodyLabel(tr("Output Folder", lang))
        layout.addWidget(self.section_output)
        output_card = CardWidget()
        out_form = QFormLayout(output_card)
        out_form.setContentsMargins(20, 16, 20, 16)
        out_form.setSpacing(12)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.output_edit = LineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(config.get("result_dir", "result"))
        self.output_browse = PushButton(FluentIcon.FOLDER, tr("Browse", lang))
        self.output_browse.clicked.connect(self._pick_output_dir)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(self.output_browse)
        self.output_label = BodyLabel(tr("Output Folder", lang))
        out_form.addRow(self.output_label, out_row)
        layout.addWidget(output_card)

        # --- Model management: unified download location + downloaded list ---
        self.section_models = StrongBodyLabel(tr("Model Management", lang))
        layout.addWidget(self.section_models)
        models_card = CardWidget()
        mv = QVBoxLayout(models_card)
        mv.setContentsMargins(20, 16, 20, 16)
        mv.setSpacing(10)
        loc_row = QHBoxLayout()
        loc_row.setSpacing(8)
        self.models_loc_label = BodyLabel(tr("Model Location", lang))
        loc_row.addWidget(self.models_loc_label)
        self.models_dir_edit = LineEdit()
        self.models_dir_edit.setReadOnly(True)
        loc_row.addWidget(self.models_dir_edit, 1)
        self.models_browse = PushButton(FluentIcon.FOLDER, tr("Change Location", lang))
        self.models_browse.clicked.connect(self._change_models_dir)
        loc_row.addWidget(self.models_browse)
        mv.addLayout(loc_row)
        self.models_list_host = QVBoxLayout()
        self.models_list_host.setSpacing(4)
        mv.addLayout(self.models_list_host)
        layout.addWidget(models_card)
        self._refresh_models()

        layout.addStretch(1)

    def _refresh_models(self):
        self.models_dir_edit.setText(model_store.current_dir())
        # Clear old rows.
        while self.models_list_host.count():
            item = self.models_list_host.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        models = model_store.list_models()
        if not models:
            self.models_list_host.addWidget(CaptionLabel(tr("No models downloaded", self._lang)))
            return
        for m in models:
            self.models_list_host.addWidget(CaptionLabel(f"• {m['label']} — {m['size_h']}"))

    def _change_models_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, tr("Model Location", self._lang), model_store.current_dir())
        if not path or os.path.abspath(path) == model_store.current_dir():
            return
        # Offer to move the already-downloaded models to the new location.
        box = MessageBox(tr("Model Management", self._lang),
                         tr("Move existing models to the new location?", self._lang), self)
        move = box.exec()
        ok, msg = model_store.set_models_dir(path, move=move)
        if ok:
            model_store.setup_model_env()
            self._refresh_models()
            self._info(tr("Model Management", self._lang),
                       tr("Restart to apply", self._lang))
        else:
            self._info(tr("Model Management", self._lang), msg, error=True)

    def _info(self, title, text, error=False):
        from qfluentwidgets import InfoBar, InfoBarPosition
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _pick_output_dir(self):
        current = self.output_edit.text() or os.getcwd()
        path = QFileDialog.getExistingDirectory(
            self, tr("Output Folder", self._lang), current)
        if path:
            self.output_edit.setText(path)
            backend.set_config("result_dir", path)

    def retranslate(self, lang):
        self._lang = lang
        self.section_translation.setText(tr("Settings", lang))
        self.per_model_hint.setText(tr("Per Model Hint", lang))
        self.section_options.setText(tr("Translation Options", lang))
        self.auto_glossary_label.setText(tr("AI Glossary Extraction", lang))
        self.section_output.setText(tr("Output Folder", lang))
        self.output_label.setText(tr("Output Folder", lang))
        self.output_browse.setText(tr("Browse", lang))
        self.section_models.setText(tr("Model Management", lang))
        self.models_loc_label.setText(tr("Model Location", lang))
        self.models_browse.setText(tr("Change Location", lang))
        self._refresh_models()
