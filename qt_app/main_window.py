"""FluentWindow with a left navigation: Translate / Glossary / Proofread /
History / Settings, plus a theme toggle. The UI-language selector lives on the
Settings page and drives a global retranslate of every page + nav label.

Theme (light/dark) is persisted to system_config.json under "qt_theme"; the UI
language under "qt_ui_lang"."""

import os

from PySide6.QtGui import QIcon

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon, setTheme, Theme,
)

from qt_app import backend
from qt_app.i18n import tr, UI_LANGS
from qt_app.translate_page import TranslatePage
from qt_app.glossary_page import GlossaryPage
from qt_app.proofread_page import ProofreadPage
from qt_app.settings_page import SettingsPage
from qt_app.history_page import HistoryPage

ICON_PATH = os.path.join(backend.REPO_ROOT, "img", "ico.png")


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # Apply persisted theme before building pages
        self._theme_dark = backend.get_config("qt_theme", "light") == "dark"
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)

        # Persisted UI language (fall back to English if unknown)
        self._lang = backend.get_config("qt_ui_lang", "en")
        if self._lang not in UI_LANGS:
            self._lang = "en"

        self.setWindowTitle("LinguaHaru")
        self.resize(1100, 760)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.translate_page = TranslatePage(self, lang=self._lang)
        self.glossary_page = GlossaryPage(self, lang=self._lang)
        self.proofread_page = ProofreadPage(self, lang=self._lang)
        self.history_page = HistoryPage(self, lang=self._lang)
        self.settings_page = SettingsPage(self, lang=self._lang)

        # routeKey -> label key, so nav text can be re-localized in place
        self._nav_keys = {
            "TranslatePage": "Translate",
            "GlossaryPage": "Glossary",
            "ProofreadPage": "Proofread",
            "HistoryPage": "History",
            "SettingsPage": "Settings",
        }

        self.addSubInterface(self.translate_page, FluentIcon.LANGUAGE,
                             tr("Translate", self._lang))
        self.addSubInterface(self.glossary_page, FluentIcon.BOOK_SHELF,
                             tr("Glossary", self._lang))
        self.addSubInterface(self.proofread_page, FluentIcon.EDIT,
                             tr("Proofread", self._lang))
        self.addSubInterface(self.history_page, FluentIcon.HISTORY,
                             tr("History", self._lang))
        self.addSubInterface(
            self.settings_page, FluentIcon.SETTING, tr("Settings", self._lang),
            position=NavigationItemPosition.BOTTOM)

        # The Settings-page language selector drives a global retranslate.
        self.settings_page.on_ui_lang_changed = self.on_lang_changed

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
        current = self.stackedWidget.currentWidget()
        if current is self.history_page:
            self.history_page.reload()
        elif current is self.proofread_page:
            self.proofread_page.refresh_docs()

    def on_lang_changed(self, lang):
        if lang not in UI_LANGS or lang == self._lang:
            return
        self._lang = lang
        backend.set_config("qt_ui_lang", lang)
        # Re-localize each page
        for page in (self.translate_page, self.glossary_page,
                     self.proofread_page, self.history_page, self.settings_page):
            page.retranslate(lang)
        # Re-localize navigation labels
        for route_key, label_key in self._nav_keys.items():
            item = self.navigationInterface.widget(route_key)
            if item is not None and hasattr(item, "setText"):
                item.setText(tr(label_key, lang))

    def toggle_theme(self):
        self._theme_dark = not self._theme_dark
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)
        backend.set_config("qt_theme", "dark" if self._theme_dark else "light")
