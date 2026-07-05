# Tests for the "Support / Contact" widget (Qt bottom-nav entry + Web sidebar
# entry/modal): assets exist and serve, all links/ids are present on the Web
# page, the i18n keys are complete, and the Qt nav item is registered.
#
# Run from the repo root:
#   python tests/test_support_widget.py
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def test_assets_exist_and_are_valid():
    qr = os.path.join(REPO_ROOT, "assets", "img", "support_qr.png")
    assert os.path.getsize(qr) > 10_000
    # PNG with an alpha channel (the corner transparency is the whole point)
    from PIL import Image
    img = Image.open(qr)
    assert img.mode == "RGBA", img.mode
    a = img.getchannel("A")
    assert a.getpixel((0, 0)) == 0, "top-left corner must be transparent"
    assert a.getpixel((img.width // 2, img.height // 2)) == 255
    ok("support_qr.png exists, RGBA, transparent corners")
    svg = open(os.path.join(REPO_ROOT, "assets", "icons", "linkedin.svg"),
               encoding="utf-8").read()
    assert "<svg" in svg and "0A66C2" in svg
    ok("linkedin.svg exists")


def test_web_page_has_all_support_elements():
    from fastapi.testclient import TestClient
    from webapp.server import app
    c = TestClient(app)
    assert c.get("/assets/img/support_qr.png").status_code == 200
    assert c.get("/assets/icons/linkedin.svg").status_code == 200
    ok("assets served over /assets")
    html = c.get("/").text
    for probe in ["support-contact", "support-modal", "support-copy-qq",
                  "support_qr.png", "linkedin.svg", "HarukaQnQ",
                  "https://www.harukayang.com/",
                  "https://www.harukayang.com/combined-pay.html",
                  "https://www.linkedin.com/in/yang-haruka/"]:
        assert probe in html, f"missing in index.html: {probe}"
    ok("index.html contains entry, modal, links, contact id")


def test_i18n_keys_complete():
    from core.languages_config import LABEL_TRANSLATIONS
    for key in ["Support / Contact", "Support Me", "My Homepage",
                "Contact Me", "Support Pay Tip", "Copied"]:
        for lang, labels in LABEL_TRANSLATIONS.items():
            assert labels.get(key), f"{lang} missing {key}"
    ok("i18n keys complete in all languages")


def test_qt_nav_item_and_urls():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from qt_app.main_window import MainWindow
    w = MainWindow()
    assert w.navigationInterface.widget("support-contact") is not None
    assert w._HOMEPAGE_URL == "https://www.harukayang.com/"
    assert w._PAY_GUIDE_URL == "https://www.harukayang.com/combined-pay.html"
    assert w._LINKEDIN_URL == "https://www.linkedin.com/in/yang-haruka/"
    assert w._CONTACT_ID == "HarukaQnQ"
    assert callable(w._show_support_menu) and callable(w._show_support_dialog)
    ok("Qt nav item registered with correct urls/id")


if __name__ == "__main__":
    test_assets_exist_and_are_valid()
    test_web_page_has_all_support_elements()
    test_i18n_keys_complete()
    test_qt_nav_item_and_urls()
    print(f"{PASS} passed, 0 failed")
