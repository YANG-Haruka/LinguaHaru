"""Desktop UI: the snapshot page transition and the glossary picker."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtGui import QPixmap

from core import backend


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_the_spring_goes_from_0_to_1_without_turning_back():
    from qt_app.page_transition import spring
    xs = [spring(i / 200) for i in range(201)]
    assert xs[0] == 0.0 and xs[-1] == 1.0 and spring(-1) == 0.0 and spring(2) == 1.0
    assert all(b >= a for a, b in zip(xs, xs[1:]))
    assert spring(0.6) > 0.9                      # most of the way early: it reads fast


def test_a_transition_animates_snapshots_and_reports_when_it_is_over(app):
    """Nothing of the real page is repainted per frame, and the page's reload
    work waits for `finished` (it used to block the first dozen frames)."""
    from qt_app import page_transition as pt
    host = QWidget(); host.resize(300, 200)
    page = QLabel("page", host); page.resize(300, 200)
    host.show(); app.processEvents()
    snap = pt.snapshot(page)
    assert snap.size().width() == 300 and snap.hasAlphaChannel()   # the sky shows through its gaps
    back = QPixmap(300, 200); back.fill()
    tr = pt.PageTransition(host); tr.setGeometry(0, 0, 300, 200)
    done = []
    tr.finished.connect(lambda: done.append(1))
    tr.run(back, snap, snap, 1)
    assert tr.running() and tr.isVisible() and not done
    tr.grab()                                    # a frame mid-way paints without error
    tr.stop()
    assert done == [1] and not tr.running() and not tr.isVisible()
    tr.stop()
    assert done == [1]                           # only once


def test_picking_a_glossary_shows_it_and_refresh_reads_the_folder_again(app, tmp_path, monkeypatch):
    """There was a separate Load button: the table kept showing the previous
    glossary until it was pressed, and a file added outside the app only showed
    up after a restart."""
    monkeypatch.setattr(backend, "GLOSSARY_DIR", str(tmp_path))
    (tmp_path / "Default.csv").write_text("en,zh\nhello,你好\n", encoding="utf-8")
    (tmp_path / "Second.csv").write_text("ja,zh\nはい,是\nいいえ,不是\n", encoding="utf-8")
    from qt_app.glossary_page import GlossaryPage
    page = GlossaryPage(None, lang="en")
    assert not hasattr(page, "load_btn")
    cell = lambda r, c: page.table.item(r, c).text()
    assert page.combo.currentText() == "Default" and cell(0, 1) == "你好"
    page.combo.setCurrentText("Second")
    assert page._header == ["ja", "zh"] and cell(1, 0) == "いいえ"          # loaded by picking it
    (tmp_path / "Third.csv").write_text("en,zh\nx,y\n", encoding="utf-8")
    (tmp_path / "Second.csv").write_text("ja,zh\nはい,对\n", encoding="utf-8")
    page.on_refresh()
    names = [page.combo.itemText(i) for i in range(page.combo.count())]
    assert "Third" in names and page.combo.currentText() == "Second" and cell(0, 1) == "对"
