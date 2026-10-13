"""Model dropdown for a plugin (OCR / STT), used on the File Translation and
Real-Time Voice pages. Lists the plugin's whole catalog; models not on disk are
marked, and picking one offers the Plugins page (where it's downloaded) instead
of switching to a model that can't run."""
from qfluentwidgets import ComboBox, MessageBox

from core.optional_modules import plugin_model_states, set_plugin_model
from qt_app.i18n import tr


class ModelPicker(ComboBox):
    def __init__(self, plugin, lang, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self._lang = lang
        self._states = []
        self._cur = -1
        self.on_open_plugins = None     # set by the page -> jump to Plugins
        self.currentIndexChanged.connect(self._picked)
        self.reload()

    def reload(self, plugin=None, lang=None):
        """Re-read the catalog (downloads / the active model may have changed)."""
        self.plugin = plugin or self.plugin
        self._lang = lang or self._lang
        self._states = plugin_model_states(self.plugin)
        self.blockSignals(True)
        self.clear()
        for s in self._states:
            name = s["label"].split(" — ")[0]
            self.addItem(name if s["downloaded"] else f"{name}（{tr('Not Downloaded', self._lang)}）")
        self._cur = next((i for i, s in enumerate(self._states) if s["active"]), 0)
        self.setCurrentIndex(self._cur)
        self.blockSignals(False)

    def _picked(self, i):
        if not (0 <= i < len(self._states)) or i == self._cur:
            return
        s = self._states[i]
        if s["downloaded"]:
            set_plugin_model(self.plugin, s["id"])
            self._cur = i
            return
        # Not on disk: keep the current model, offer the Plugins page.
        self.blockSignals(True)
        self.setCurrentIndex(self._cur)
        self.blockSignals(False)
        msg = tr("Model Not Downloaded Go", self._lang).format(model=s["label"].split(" — ")[0])
        if MessageBox(tr("Plugins", self._lang), msg, self.window()).exec() and callable(self.on_open_plugins):
            self.on_open_plugins()
