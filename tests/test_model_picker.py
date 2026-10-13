# Model control on the File Translation / Real-Time Voice pages: a button with
# the active model + whether it's on disk, opening the Plugins page's model list
# where every model shows its state and is switched / downloaded in place.
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from qfluentwidgets import PushButton

from core import optional_modules as om
from qt_app import model_picker as mp, plugins_page as pp

STATES = [
    {"id": "small", "label": "PP-OCRv6 Small", "tags": [], "size": "100 MB", "downloaded": True},
    {"id": "medium", "label": "PP-OCRv6 Medium", "tags": [], "size": "140 MB", "downloaded": True},
    {"id": "tiny", "label": "PP-OCRv6 Tiny", "tags": [], "size": "55 MB", "downloaded": False},
]


def _fake(monkeypatch, active):
    cur = {"id": active}
    def states(name, with_size=False):
        return [dict(s, active=s["id"] == cur["id"], disk_human="1 MB") for s in STATES]
    monkeypatch.setattr(om, "plugin_model_states", states)
    monkeypatch.setattr(mp, "plugin_model_states", states)
    monkeypatch.setattr(om, "plugin_current_model", lambda name: cur["id"])
    monkeypatch.setattr(om, "set_plugin_model", lambda name, mid: cur.update(id=mid))
    return cur


def _texts(w):
    return [b.text() for b in w.findChildren(PushButton)]


def test_button_shows_state(monkeypatch):
    _fake(monkeypatch, "tiny")
    b = mp.ModelPicker("Image OCR", "zh")
    assert b._name.text() == "PP-OCRv6 Tiny" and b._state.text() == "未下载"
    _fake(monkeypatch, "small")
    b.reload()
    assert b._state.text() == "已下载"


def test_list_switches_and_downloads(monkeypatch):
    cur = _fake(monkeypatch, "small")
    from PySide6.QtWidgets import QWidget
    host = QWidget(); host.resize(800, 600)
    dlg = pp._ModelPickerDialog("Image OCR", "zh", parent=host)
    buttons = dlg.widget.findChildren(PushButton)
    assert "下载" in _texts(dlg.widget) and "使用" in _texts(dlg.widget)   # tiny / medium

    next(b for b in buttons if b.text() == "使用").click()            # medium
    assert cur["id"] == "medium"

    started = []
    class W:                                   # stand-in for ModelDownloadWorker
        def __init__(self, plugin, mid, parent=None):
            started.append(mid)
            from PySide6.QtCore import QObject, Signal
            class Sig(QObject):
                progress = Signal(float, str); detail = Signal(str); finished_ok = Signal(bool)
            self._s = Sig()
            self.progress, self.detail, self.finished_ok = self._s.progress, self._s.detail, self._s.finished_ok
        def start(self):
            pass
    monkeypatch.setattr(pp, "ModelDownloadWorker", W)
    app.processEvents()   # the rebuilt rows replace the old ones
    dl = next(b for b in dlg.widget.findChildren(PushButton) if b.text() == "下载" and b.isEnabled())
    dl.click()
    assert started == ["tiny"]
    assert not dlg.yesButton.isEnabled()      # can't close mid-download (would kill the worker)
    cur["id"] = "tiny"; STATES[2]["downloaded"] = True
    try:
        dlg._workers[-1].finished_ok.emit(True)
        assert dlg.yesButton.isEnabled()
    finally:
        STATES[2]["downloaded"] = False


def test_ocr_size_follows_plugin_choice(monkeypatch):
    from core.pipelines import image_translation_pipeline as ip
    monkeypatch.setattr(om, "_cfg_read", lambda: {"ocr_model_size": "tiny"})
    # Manga keeps its own default (medium), exactly what the Plugins page shows.
    assert (ip._ocr_model_size(False), ip._ocr_model_size(True)) == ("tiny", "medium")
