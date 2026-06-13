"""FluentWindow with a left navigation: Translate / Glossary / Settings / History.

Theme (light/dark) is persisted to system_config.json under "qt_theme"."""

import os

from PySide6.QtGui import QIcon
from PySide6.QtCore import QSize

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon, setTheme, Theme,
)

from qt_app import backend
from qt_app.translate_page import TranslatePage
from qt_app.glossary_page import GlossaryPage
from qt_app.settings_page import SettingsPage
from qt_app.history_page import HistoryPage

ICON_PATH = os.path.join(backend.REPO_ROOT, "img", "ico.png")


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # Apply persisted theme before building pages
        self._theme_dark = backend.get_config("qt_theme", "light") == "dark"
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)

        self.setWindowTitle("LinguaHaru")
        self.resize(1100, 760)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.translate_page = TranslatePage(self)
        self.glossary_page = GlossaryPage(self)
        self.settings_page = SettingsPage(self)
        self.history_page = HistoryPage(self)

        self.addSubInterface(self.translate_page, FluentIcon.LANGUAGE, "Translate")
        self.addSubInterface(self.glossary_page, FluentIcon.BOOK_SHELF, "Glossary")
        self.addSubInterface(self.history_page, FluentIcon.HISTORY, "History")
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, "Settings",
            position=NavigationItemPosition.BOTTOM)

        # Theme toggle pinned at the bottom of the navigation rail
        self.navigationInterface.addItem(
            routeKey="theme-toggle",
            icon=FluentIcon.CONSTRACT,
            text="Theme",
            onClick=self.toggle_theme,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

        # Reload history whenever its tab becomes current
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

    def _on_page_changed(self, _index):
        if self.stackedWidget.currentWidget() is self.history_page:
            self.history_page.reload()

    def toggle_theme(self):
        self._theme_dark = not self._theme_dark
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)
        backend.set_config("qt_theme", "dark" if self._theme_dark else "light")
