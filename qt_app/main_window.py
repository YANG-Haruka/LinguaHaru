"""FluentWindow with a grouped left navigation (AiNiee-style sections):

    Quick Start : Interface Management, Translate
    Advanced    : Settings, History, Proofread
    Vocabulary  : Glossary
    (standalone): Plugins
    BOTTOM      : Theme toggle

Theme (light/dark) is persisted to system_config.json under "qt_theme"; the UI
language under "qt_ui_lang". The Settings-page language selector drives a global
retranslate of every page + nav label."""

import os

from PySide6.QtGui import QIcon, QColor, QCursor

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon, setTheme, setThemeColor,
    Theme, RoundMenu, Action,
)

from qt_app import backend
from qt_app.i18n import tr, UI_LANGS, lang_display_name
from qt_app.translate_page import TranslatePage
from qt_app.glossary_page import GlossaryPage
from qt_app.proofread_page import ProofreadPage
from qt_app.settings_page import SettingsPage
from qt_app.history_page import HistoryPage
from qt_app.interface_page import InterfacePage
from qt_app.plugins_page import PluginsPage

ICON_PATH = os.path.join(backend.REPO_ROOT, "img", "ico.png")
ACCENT_COLOR = "#2f6fed"          # blue accent (both themes)
LIGHT_BG = "#d7e6fb"             # light: clearly light-blue
DARK_BG = "#0b1120"             # dark: deep navy-black


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # Disable the Windows Mica/acrylic backdrop: with it on, the content
        # area shows the system backdrop (dark when Windows is in dark mode)
        # even while our app theme is light -> the "light nav + dark content"
        # split. A solid themed background is consistent in both modes.
        try:
            self.setMicaEffectEnabled(False)
        except Exception:
            pass

        # Apply persisted theme + accent before building pages.
        self._theme_dark = backend.get_config("qt_theme", "light") == "dark"
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)
        setThemeColor(ACCENT_COLOR)
        self._apply_custom_bg()

        # Persisted UI language (default zh; fall back if unknown).
        self._lang = backend.get_config("qt_ui_lang", "zh")
        if self._lang not in UI_LANGS:
            self._lang = "zh"

        self.setWindowTitle("LinguaHaru")
        self.resize(1200, 800)
        # Minimum size large enough that the expanded nav + content fit without
        # the right edge getting cut off.
        self.setMinimumSize(1100, 720)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.interface_page = InterfacePage(self, lang=self._lang)
        self.translate_page = TranslatePage(self, lang=self._lang)
        self.settings_page = SettingsPage(self, lang=self._lang)
        self.history_page = HistoryPage(self, lang=self._lang)
        self.proofread_page = ProofreadPage(self, lang=self._lang)
        self.glossary_page = GlossaryPage(self, lang=self._lang)
        self.plugins_page = PluginsPage(self, lang=self._lang)

        # routeKey -> label key, so nav text can be re-localized in place
        self._nav_keys = {
            "InterfacePage": "Interface Management",
            "TranslatePage": "Translate",
            "SettingsPage": "Settings",
            "HistoryPage": "History",
            "ProofreadPage": "Proofread",
            "GlossaryPage": "Glossary",
            "PluginsPage": "Plugins",
        }
        # group-header routeKeys -> label key (gray section titles)
        self._header_keys = {}

        nav = self.navigationInterface

        # --- Quick Start group ---
        self._add_header("hdr_quick", "Quick Start")
        self.addSubInterface(self.interface_page, FluentIcon.CONNECT,
                             tr("Interface Management", self._lang))
        self.addSubInterface(self.translate_page, FluentIcon.LANGUAGE,
                             tr("Translate", self._lang))

        # --- Advanced group ---
        self._add_header("hdr_advanced", "Advanced")
        self.addSubInterface(self.settings_page, FluentIcon.SETTING,
                             tr("Settings", self._lang))
        self.addSubInterface(self.history_page, FluentIcon.HISTORY,
                             tr("History", self._lang))
        self.addSubInterface(self.proofread_page, FluentIcon.EDIT,
                             tr("Proofread", self._lang))

        # --- Vocabulary group ---
        self._add_header("hdr_vocab", "Vocabulary")
        self.addSubInterface(self.glossary_page, FluentIcon.DICTIONARY,
                             tr("Glossary", self._lang))

        # --- standalone ---
        nav.addSeparator()
        self.addSubInterface(self.plugins_page, FluentIcon.APPLICATION,
                             tr("Plugins", self._lang))

        # Cross-page wiring.
        self.settings_page.on_ui_lang_changed = self.on_lang_changed
        self.interface_page.on_active_changed = self.translate_page.refresh_active_interface
        # Clicking an unavailable format card jumps to the Plugins page.
        self.translate_page.on_open_plugins = lambda: self.switchTo(self.plugins_page)

        # Interface-language picker + theme toggle pinned at the bottom of the
        # navigation rail (language above theme).
        nav.addItem(
            routeKey="ui-lang",
            icon=FluentIcon.GLOBE,
            text=tr("Interface Language", self._lang),
            onClick=self._show_lang_menu,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        nav.addItem(
            routeKey="theme-toggle",
            icon=FluentIcon.CONSTRACT,
            text=tr("Theme", self._lang),
            onClick=self.toggle_theme,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

        # Keep the navigation rail expanded by default (like AiNiee) so the
        # text labels + group headers are always visible, instead of the
        # width-dependent auto-collapse.
        nav_iface = self.navigationInterface
        try:
            nav_iface.setExpandWidth(250)
            nav_iface.setMinimumExpandWidth(820)  # window is 1100 wide
            if hasattr(nav_iface, "expand"):
                nav_iface.expand(useAni=False)
        except Exception:
            pass

        # Default to the Translate page on launch.
        self.switchTo(self.translate_page)

        # Reload data whenever a tab becomes current.
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

    def _show_lang_menu(self):
        """Dropdown of interface languages, opened from the bottom nav item."""
        menu = RoundMenu(parent=self)
        for lang in UI_LANGS:
            act = Action(lang_display_name(lang))
            act.triggered.connect(lambda _checked=False, l=lang: self.on_lang_changed(l))
            menu.addAction(act)
        menu.exec(QCursor.pos())

    def _add_header(self, route_key, label_key):
        """Add a gray, non-clickable section header to the nav rail."""
        self.navigationInterface.addItemHeader(tr(label_key, self._lang))
        self._header_keys[route_key] = label_key

    def _on_page_changed(self, _index):
        current = self.stackedWidget.currentWidget()
        if current is self.history_page:
            self.history_page.reload()
        elif current is self.proofread_page:
            self.proofread_page.refresh_docs()
        elif current is self.interface_page:
            self.interface_page.reload()
        elif current is self.translate_page:
            self.translate_page.refresh_active_interface()
            # Reflect plugins that may have been installed since last view.
            self.translate_page._refresh_format_availability()

    def on_lang_changed(self, lang):
        if lang not in UI_LANGS or lang == self._lang:
            return
        self._lang = lang
        backend.set_config("qt_ui_lang", lang)
        # Re-localize each page.
        for page in (self.interface_page, self.translate_page, self.settings_page,
                     self.history_page, self.proofread_page, self.glossary_page,
                     self.plugins_page):
            page.retranslate(lang)
        # Re-localize navigation labels.
        for route_key, label_key in self._nav_keys.items():
            item = self.navigationInterface.widget(route_key)
            if item is not None and hasattr(item, "setText"):
                item.setText(tr(label_key, lang))
        theme_item = self.navigationInterface.widget("theme-toggle")
        if theme_item is not None and hasattr(theme_item, "setText"):
            theme_item.setText(tr("Theme", lang))
        lang_item = self.navigationInterface.widget("ui-lang")
        if lang_item is not None and hasattr(lang_item, "setText"):
            lang_item.setText(tr("Interface Language", lang))

    def _apply_custom_bg(self):
        """Background follows the plain qfluentwidgets theme.

        NOTE: setCustomBackgroundColor() was removed — with the Mica effect
        disabled it forced the content area dark even in light mode (the
        'light nav + dark content' split). Plain theming renders a consistent
        light/dark surface; the blue identity comes from the accent color."""
        return

    def toggle_theme(self):
        self._theme_dark = not self._theme_dark
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)
        setThemeColor(ACCENT_COLOR)
        self._apply_custom_bg()
        backend.set_config("qt_theme", "dark" if self._theme_dark else "light")
        # Repaint the colorful format cards (their tint depends on theme).
        for card in getattr(self.translate_page, "_fmt_cards", []):
            card.refresh_theme()
