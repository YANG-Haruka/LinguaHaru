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
    for name, brand_color in [("linkedin.svg", "0A66C2"),
                              ("qq.svg", "12B7F5"),
                              ("wechat.svg", "07C160")]:
        svg = open(os.path.join(REPO_ROOT, "assets", "icons", name),
                   encoding="utf-8").read()
        assert "<svg" in svg and brand_color in svg, name
    ok("linkedin/qq/wechat brand SVGs exist")


def test_web_page_has_all_support_elements():
    from fastapi.testclient import TestClient
    from webapp.server import app
    c = TestClient(app)
    assert c.get("/assets/img/support_qr.png").status_code == 200
    for icon in ["linkedin.svg", "qq.svg", "wechat.svg"]:
        assert c.get(f"/assets/icons/{icon}").status_code == 200, icon
    ok("assets served over /assets")
    html = c.get("/").text
    for probe in ["support-contact", "support-modal",
                  "support-copy-qq", "support-copy-wechat",
                  "support_qr.png", "linkedin.svg", "qq.svg", "wechat.svg",
                  "QQ：3234306205", "微信：HarukaQnQ",
                  "https://www.harukayang.com/",
                  "https://www.harukayang.com/combined-pay.html",
                  "https://www.linkedin.com/in/yang-haruka/"]:
        assert probe in html, f"missing in index.html: {probe}"
    ok("index.html contains entry, modal, links, separate QQ/WeChat ids")


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
    assert w._QQ_ID == "3234306205"
    assert w._WECHAT_ID == "HarukaQnQ"
    assert callable(w._show_support_menu) and callable(w._show_support_dialog)
    ok("Qt nav item registered with correct urls + separate QQ/WeChat ids")


if __name__ == "__main__":
    test_assets_exist_and_are_valid()
    test_web_page_has_all_support_elements()
    test_i18n_keys_complete()
    test_qt_nav_item_and_urls()
    print(f"{PASS} passed, 0 failed")
