# Model dropdowns on the File Translation / Real-Time Voice pages: the whole
# catalog is listed, but only a downloaded model may become active; picking one
# that isn't on disk keeps the current model and offers the Plugins page.
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from qt_app import model_picker as mp

STATES = [
    {"id": "small", "label": "PP-OCRv6 Small", "downloaded": True, "active": True},
    {"id": "medium", "label": "PP-OCRv6 Medium", "downloaded": True, "active": False},
    {"id": "tiny", "label": "PP-OCRv6 Tiny", "downloaded": False, "active": False},
]


def test_picker(monkeypatch):
    saved, asked, jumped = [], [], []
    monkeypatch.setattr(mp, "plugin_model_states", lambda name: STATES)
    monkeypatch.setattr(mp, "set_plugin_model", lambda name, mid: saved.append((name, mid)))

    class Box:
        def __init__(self, title, msg, parent):
            asked.append(msg)

        def exec(self):
            return True
    monkeypatch.setattr(mp, "MessageBox", Box)

    p = mp.ModelPicker("Image OCR", "zh")
    p.on_open_plugins = lambda: jumped.append(1)
    assert p.currentIndex() == 0
    assert p.itemText(2).startswith("PP-OCRv6 Tiny") and p.itemText(2) != "PP-OCRv6 Tiny"   # marked

    p.setCurrentIndex(2)                     # not downloaded
    assert p.currentIndex() == 0 and not saved and jumped == [1]
    assert "PP-OCRv6 Tiny" in asked[0]

    p.setCurrentIndex(1)                     # downloaded -> becomes active
    assert saved == [("Image OCR", "medium")] and p.currentIndex() == 1

    p.setCurrentIndex(2)                     # reverts to the NEW current one
    assert p.currentIndex() == 1 and len(saved) == 1


def test_ocr_size_follows_plugin_choice(monkeypatch):
    from core import optional_modules as om
    from core.pipelines import image_translation_pipeline as ip
    monkeypatch.setattr(om, "_cfg_read", lambda: {"ocr_model_size": "tiny"})
    # Manga keeps its own default (medium), exactly what the Plugins page shows.
    assert (ip._ocr_model_size(False), ip._ocr_model_size(True)) == ("tiny", "medium")
