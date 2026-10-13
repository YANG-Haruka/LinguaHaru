"""Model control for a plugin (OCR / STT) on the File Translation and Real-Time
Voice pages: a dropdown-looking button with the active model and whether it's
on disk. Clicking opens the same model list as the Plugins page (state of every
model; switch to a downloaded one, download a missing one in place)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, IconWidget, PushButton

from core.optional_modules import plugin_model_states
from qt_app.i18n import tr


class ModelPicker(PushButton):
    def __init__(self, plugin, lang, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self._lang = lang
        self.setMinimumHeight(33)
        h = QHBoxLayout(self)
        h.setContentsMargins(11, 0, 10, 0)
        h.setSpacing(8)
        self._name = BodyLabel()
        self._state = CaptionLabel()
        chev = IconWidget(FluentIcon.CHEVRON_DOWN_MED)
        chev.setFixedSize(10, 10)
        for w in (self._name, self._state, chev):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        h.addWidget(self._name, 1)
        h.addWidget(self._state)
        h.addWidget(chev)
        self.clicked.connect(self._open)
        self.reload()

    def reload(self, plugin=None, lang=None):
        """Re-read the active model (it may have been switched / downloaded)."""
        self.plugin = plugin or self.plugin
        self._lang = lang or self._lang
        states = plugin_model_states(self.plugin)
        cur = next((s for s in states if s["active"]), states[0] if states else None)
        if cur is None:
            return
        self._name.setText(tr(cur["label"], self._lang).partition(" — ")[0])
        ok = cur["downloaded"]
        self._state.setText(tr("Downloaded" if ok else "Not Downloaded", self._lang))
        self._state.setTextColor(*(("#15803d", "#4ade80") if ok else ("#a16207", "#facc15")))

    def _open(self):
        from qt_app.plugins_page import _ModelPickerDialog
        _ModelPickerDialog(self.plugin, self._lang, parent=self.window()).exec()
        self.reload()
