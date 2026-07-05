"""Render the Qt UI to PNGs for human review (headless / offscreen).

    QT_QPA_PLATFORM=offscreen python tests/_screenshots.py

Saves to tests/_ui_shots/ : the main window on the Translate page and each
individual page, in both light and dark themes. Uses widget.grab().save()
which works under the offscreen platform. The output dir is git-ignored; the
files are left on disk for review.

A single MainWindow is reused: the global qfluentwidgets theme is a process
singleton, so we drive light/dark via setTheme + the window's card refresh and
capture between processEvents() passes. The persisted qt_theme is restored at
the end so running this doesn't change the user's config.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

OUT_DIR = os.path.join(REPO_ROOT, "tests", "_ui_shots")
os.makedirs(OUT_DIR, exist_ok=True)


def _save(widget, name, app):
    app.processEvents()
    app.processEvents()
    widget.grab().save(os.path.join(OUT_DIR, name))
    print("  wrote", os.path.join("tests", "_ui_shots", name))


def _prime_dashboard(window):
    window.translate_page._total = 5
    window.translate_page.dashboard.start()
    window.translate_page.dashboard.update_metrics(
        percent=62, total_files=5, done_files=3, live_tasks=2,
        failed=1, total_tokens=128450)
    window.translate_page.setCurrentWidget(window.translate_page.dashboard)


def main():
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, setThemeColor, Theme
    from core import backend
    from qt_app.main_window import MainWindow, ACCENT_COLOR

    saved_theme = backend.get_config("qt_theme", "light")

    app = QApplication.instance() or QApplication([])

    def apply_theme(theme):
        setTheme(theme)
        setThemeColor(ACCENT_COLOR)
        for card in getattr(w.translate_page, "_fmt_cards", []):
            card.refresh_theme()
        app.processEvents()

    w = MainWindow()
    w.resize(1100, 720)
    w.show()
    app.processEvents()

    # --- LIGHT ---
    apply_theme(Theme.LIGHT)
    w.switchTo(w.translate_page)
    w.translate_page.setCurrentWidget(w.translate_page._controls)
    _save(w, "main_translate_light.png", app)
    w.switchTo(w.interface_page)
    _save(w, "interface_light.png", app)
    w.switchTo(w.plugins_page)
    _save(w, "plugins_light.png", app)
    w.switchTo(w.settings_page)
    _save(w, "settings_light.png", app)
    w.switchTo(w.glossary_page)
    _save(w, "glossary_light.png", app)
    w.switchTo(w.translate_page)
    _prime_dashboard(w)
    _save(w, "progress_dashboard_light.png", app)

    # --- DARK ---
    w.translate_page.setCurrentWidget(w.translate_page._controls)
    apply_theme(Theme.DARK)
    w.switchTo(w.translate_page)
    _save(w, "main_translate_dark.png", app)
    w.switchTo(w.interface_page)
    _save(w, "interface_dark.png", app)
    w.switchTo(w.plugins_page)
    _save(w, "plugins_dark.png", app)
    w.switchTo(w.settings_page)
    _save(w, "settings_dark.png", app)
    w.switchTo(w.translate_page)
    _prime_dashboard(w)
    _save(w, "progress_dashboard_dark.png", app)

    # restore persisted theme (don't mutate the user's config)
    backend.set_config("qt_theme", saved_theme)
    print(f"\nDone. {len([f for f in os.listdir(OUT_DIR) if f.endswith('.png')])}"
          f" PNG(s) in {OUT_DIR}")


if __name__ == "__main__":
    main()
