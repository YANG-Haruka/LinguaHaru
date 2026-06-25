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

from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QIcon, QColor, QCursor, QDesktopServices

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon, setTheme, setThemeColor,
    Theme, RoundMenu, Action, MessageBox,
)


class _UpdateCheckWorker(QThread):
    """Background update check so startup never blocks on the network."""
    done = Signal(dict)

    def run(self):
        try:
            from core.updater import check_for_update
            res = check_for_update()
            if res:
                self.done.emit(res)
        except Exception:
            pass


class _SelfUpdateWorker(QThread):
    """Download + apply the portable in-place update, reporting progress."""
    progress = Signal(float, str)
    finished_ok = Signal(bool, str)

    def __init__(self, asset_url, sha256, parent=None, asset_urls=None):
        super().__init__(parent)
        self._asset = asset_url
        self._sha = sha256
        self._asset_urls = asset_urls

    def run(self):
        try:
            from core.updater import download_and_apply
            ok, msg = download_and_apply(
                self._asset, self._sha, lambda f, s="": self.progress.emit(float(f), s),
                asset_urls=self._asset_urls)
            self.finished_ok.emit(ok, msg)
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(False, str(e))

from core import backend
from qt_app.i18n import tr, UI_LANGS, lang_display_name
from qt_app.translate_page import TranslatePage
from qt_app.quick_page import QuickPage
from qt_app.live_page import LivePage
from qt_app.glossary_page import GlossaryPage
from qt_app.proofread_page import ProofreadPage
from qt_app.settings_page import SettingsPage
from qt_app.history_page import HistoryPage
from qt_app.interface_page import InterfacePage
from qt_app.plugins_page import PluginsPage
from qt_app.sky_background import SkyBackground

ICON_PATH = os.path.join(backend.REPO_ROOT, "assets", "img", "ico.png")
# Accent matches the redesigned Web UI's sky-blue identity (theme-aware: a
# deeper sky in light mode, a brighter sky that glows on the dark surface).
ACCENT_LIGHT = "#0d83d6"          # sky blue — matches Web light theme
ACCENT_DARK = "#3f9bff"           # brighter sky — matches Web dark theme


def _accent_for(dark):
    return ACCENT_DARK if dark else ACCENT_LIGHT


LIGHT_BG = "#eaf1fa"             # light: clearly light-blue
DARK_BG = "#04070e"             # dark: deep navy-black


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
        setThemeColor(_accent_for(self._theme_dark))
        self._apply_custom_bg()

        # Animated sky behind the (transparent) content area — the desktop twin
        # of the Web UI's background canvas. Created before the pages so it sits
        # at the bottom of the z-order; re-lowered after build to be safe.
        self._sky = SkyBackground(self, mode="night" if self._theme_dark else "day")
        self._sky.setGeometry(0, 0, self.width(), self.height())

        # Persisted UI language (default zh; fall back if unknown).
        self._lang = backend.get_config("qt_ui_lang", "zh")
        if self._lang not in UI_LANGS:
            self._lang = "zh"

        self.setWindowTitle("LinguaHaru")
        self.resize(1200, 800)
        # Allow the window to shrink enough that the nav auto-collapses (the
        # format row + pages scroll, so a narrow window is fine).
        self.setMinimumSize(720, 600)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        # Build only the landing page (Translate/quick) and the page before it in
        # the nav (Interface) eagerly; the heavier pages (Plugins ~220ms, etc.)
        # stream in right after the window is shown — see _build_deferred_pages —
        # so the window appears almost instantly. They all come AFTER quick in the
        # nav order, so appending them later preserves the order.
        self.interface_page = InterfacePage(self, lang=self._lang)
        self.quick_page = QuickPage(self, lang=self._lang)
        self.translate_page = None
        self.live_page = None
        self.settings_page = None
        self.history_page = None
        self.proofread_page = None
        self.glossary_page = None
        self.plugins_page = None
        self._deferred_built = False

        # routeKey -> label key, so nav text can be re-localized in place
        self._nav_keys = {
            "InterfacePage": "Interface Management",
            "QuickPage": "Translate",
            "TranslatePage": "File Translation",
            "LivePage": "Real-Time Voice",
            "SettingsPage": "Settings",
            "HistoryPage": "History",
            "ProofreadPage": "Proofread",
            "GlossaryPage": "Glossary",
            "PluginsPage": "Plugins",
        }
        # group-header routeKeys -> label key (gray section titles)
        self._header_keys = {}

        nav = self.navigationInterface

        # Nav order/grouping (separators between the 5 groups), per user spec:
        #   1) Interface Management
        #   2) Translate (quick), File Translation, Real-Time Voice
        #   3) Glossary, Proofread, History
        #   4) Plugins
        #   5) Settings
        self.addSubInterface(self.interface_page, FluentIcon.CONNECT,
                             tr("Interface Management", self._lang))

        nav.addSeparator()
        self.addSubInterface(self.quick_page, FluentIcon.SEND,
                             tr("Translate", self._lang))
        # The remaining pages (File Translation, Real-Time Voice, Glossary,
        # Proofread, History, Plugins, Settings) are added in _build_deferred_pages
        # after the window is shown.

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

        # Navigation rail: expanded (text labels + group headers) when the
        # window is wide, auto-collapsed to icons when it gets narrow. The
        # initial state is set from the starting width below.
        nav_iface = self.navigationInterface
        try:
            nav_iface.setExpandWidth(250)
            nav_iface.setMinimumExpandWidth(720)
        except Exception:
            pass
        self._auto_nav(animate=False)

        # Let the animated sky show through the content area: keep it at the
        # bottom of the z-order and make the page stack transparent (the nav
        # rail stays opaque so its labels remain legible). The pages themselves
        # already use transparent ScrollAreas, so only the cards are painted.
        self._sky.lower()
        try:
            self.stackedWidget.setStyleSheet("background: transparent;")
        except Exception:
            pass

        # Default to the Translate page (quick translate) on launch.
        self.switchTo(self.quick_page)

        # Reload data whenever a tab becomes current.
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

        # Freeze the animated sky while a page slides in: the page-switch
        # animation then gets the full frame budget instead of competing with a
        # 30fps full-window background repaint, so transitions feel smooth.
        try:
            view = self.stackedWidget.view  # the PopUpAniStackedWidget
            view.aniStart.connect(self._sky.pause)
            view.aniFinished.connect(self._sky.resume)
        except Exception:
            pass

        # Check for a newer version in the background (China-friendly mirrors).
        self._update_worker = _UpdateCheckWorker()
        self._update_worker.done.connect(self._on_update_checked)
        self._update_worker.start()

        # Stream the remaining (heavier) pages in right after the first paint, so
        # the window appears almost instantly instead of waiting for every page.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._build_deferred_pages)

    def _build_deferred_pages(self):
        """Construct the heavier pages ONE PER EVENT-LOOP TICK after the window is
        visible. Building all seven synchronously froze the UI thread ~1.2s on
        first paint (Settings ~0.6s + Plugins ~0.3s dominate); streaming them in
        keeps the window responsive — the eager landing page is usable immediately
        and the nav menu fills in over ~1s. Steps run in nav order so each append
        preserves the order/grouping."""
        if self._deferred_built:
            return
        self._deferred_built = True
        nav = self.navigationInterface

        def _s_translate():
            self.translate_page = TranslatePage(self, lang=self._lang)
            self.addSubInterface(self.translate_page, FluentIcon.LANGUAGE,
                                 tr("File Translation", self._lang))

        def _s_live():
            self.live_page = LivePage(self, lang=self._lang)
            self.addSubInterface(self.live_page, FluentIcon.MICROPHONE,
                                 tr("Real-Time Voice", self._lang))
            nav.addSeparator()

        def _s_glossary():
            self.glossary_page = GlossaryPage(self, lang=self._lang)
            self.addSubInterface(self.glossary_page, FluentIcon.DICTIONARY,
                                 tr("Glossary", self._lang))

        def _s_proofread():
            self.proofread_page = ProofreadPage(self, lang=self._lang)
            self.addSubInterface(self.proofread_page, FluentIcon.EDIT,
                                 tr("Proofread", self._lang))

        def _s_history():
            self.history_page = HistoryPage(self, lang=self._lang)
            self.addSubInterface(self.history_page, FluentIcon.HISTORY,
                                 tr("History", self._lang))
            nav.addSeparator()

        def _s_plugins():
            self.plugins_page = PluginsPage(self, lang=self._lang)
            self.addSubInterface(self.plugins_page, FluentIcon.APPLICATION,
                                 tr("Plugins", self._lang))
            nav.addSeparator()

        def _s_settings():
            self.settings_page = SettingsPage(self, lang=self._lang)
            self.addSubInterface(self.settings_page, FluentIcon.SETTING,
                                 tr("Settings", self._lang))
            self._wire_deferred_pages()

        self._build_steps = [_s_translate, _s_live, _s_glossary, _s_proofread,
                             _s_history, _s_plugins, _s_settings]
        self._drain_build_steps()

    def _drain_build_steps(self):
        """Run the next deferred-page build step, then yield to the event loop
        (paint/input) before the next — so the window never freezes for the whole
        build."""
        from PySide6.QtCore import QTimer
        if not getattr(self, "_build_steps", None):
            return
        step = self._build_steps.pop(0)
        step()
        if self._build_steps:
            QTimer.singleShot(0, self._drain_build_steps)

    def _wire_deferred_pages(self):
        """Cross-page wiring + first navigation + onboarding, run once the last
        deferred page (Settings) exists."""
        # Cross-page wiring (now that the pages exist).
        self.settings_page.on_ui_lang_changed = self.on_lang_changed
        self.interface_page.on_active_changed = self.translate_page.refresh_active_interface
        self.translate_page.on_open_plugins = lambda: self.switchTo(self.plugins_page)
        self.translate_page.on_open_interface = lambda: self.switchTo(self.interface_page)
        self.quick_page.on_open_plugins = lambda: self.switchTo(self.plugins_page)
        self.quick_page.on_open_interface = lambda: self.switchTo(self.interface_page)
        self.live_page.on_open_plugins = lambda: self.switchTo(self.plugins_page)

        def _continue_on_dashboard(worker, name):
            if self.translate_page.adopt_resume_worker(worker, name):
                self.switchTo(self.translate_page)
                return True
            return False
        self.history_page.on_continue_resume = _continue_on_dashboard

        def _continue_batch_on_dashboard(workers):
            if self.translate_page.adopt_resume_batch(workers):
                self.switchTo(self.translate_page)
                return True
            return False
        self.history_page.on_continue_resume_batch = _continue_batch_on_dashboard
        self._auto_nav(animate=False)

        # First-run onboarding tutorial (once per install; deferred so it pops
        # over a fully-painted window).
        from PySide6.QtCore import QTimer
        QTimer.singleShot(120, self._maybe_onboard)

    def _maybe_onboard(self):
        from qt_app.onboarding import maybe_show_onboarding
        maybe_show_onboarding(self, self._lang)

    def closeEvent(self, event):
        """Deterministically stop every page's background QThreads before the app
        tears down — otherwise a running mic/STT/translate/update thread is
        destroyed mid-run and Qt aborts ('QThread: Destroyed while thread is
        still running')."""
        if self.live_page:
            self.live_page._shutting_down = True   # save the transcript but skip the modal
        for fn in (
            lambda: self.live_page and self.live_page.on_stop(),
            lambda: self.quick_page.shutdown(),
            lambda: self.translate_page and self.translate_page.shutdown(),
            lambda: self.plugins_page and self.plugins_page.shutdown(),
            lambda: (self._update_worker.isRunning() and self._update_worker.wait(2000)),
            lambda: (getattr(self, "_self_update_worker", None)
                     and self._self_update_worker.isRunning()
                     and self._self_update_worker.wait(3000)),
        ):
            try:
                fn()
            except Exception:  # noqa: BLE001 — shutdown must never block exit
                pass
        # Backstop: wait on any QThread still running anywhere under the window
        # (catches future/transient workers the per-page cleanup missed).
        from PySide6.QtCore import QThread
        for t in self.findChildren(QThread):
            try:
                if t.isRunning():
                    t.requestInterruption()
                    t.wait(2000)
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    # Width at/above which the nav rail expands; below it, it collapses to icons.
    _NAV_EXPAND_THRESHOLD = 940

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_sky"):
            self._sky.setGeometry(0, 0, self.width(), self.height())
            self._sky.lower()
        self._auto_nav(animate=True)

    def _auto_nav(self, animate=True):
        """Expand the nav rail when the window is wide, collapse it when narrow.

        Tracks the *desired* state in ``self._nav_expanded`` rather than reading
        the panel's animated displayMode, so it fires exactly once per crossing
        and survives mid-animation resizes."""
        # resizeEvent can fire during super().__init__(), before the nav exists.
        nav = getattr(self, "navigationInterface", None)
        panel = getattr(nav, "panel", None) if nav is not None else None
        if panel is None:
            return
        want_expanded = self.width() >= self._NAV_EXPAND_THRESHOLD
        if want_expanded == getattr(self, "_nav_expanded", None):
            return
        self._nav_expanded = want_expanded
        try:
            if want_expanded:
                panel.expand(useAni=animate)
            else:
                panel.collapse()
        except Exception:
            pass

    def _on_update_checked(self, info):
        if not info or not info.get("update"):
            return
        title = tr("Update Available", self._lang)
        body = tr("Update Prompt", self._lang).format(
            version=info.get("latest", ""), current=info.get("current", ""))
        notes = info.get("notes")
        if notes:
            body += "\n\n" + str(notes)
        asset = info.get("asset_url")
        sha = info.get("asset_sha256")
        box = MessageBox(title, body, self)
        # Portable build with a verified package (url + sha256) -> one-click in-app
        # update that keeps installed plugins + models. Else open the download page.
        box.yesButton.setText(tr("Update Now", self._lang) if (asset and sha)
                              else tr("Go to Download", self._lang))
        box.cancelButton.setText(tr("Later", self._lang))
        if not box.exec():
            return
        if asset and sha:
            self._start_self_update(asset, sha, info.get("asset_urls"))
        else:
            QDesktopServices.openUrl(QUrl(info.get("url") or ""))

    def _start_self_update(self, asset, sha256, asset_urls=None):
        from qfluentwidgets import StateToolTip, InfoBar, InfoBarPosition
        self._update_tip = StateToolTip(tr("Updating", self._lang), "0%", self)
        self._update_tip.move(self.width() - 240, 20)
        self._update_tip.show()
        self._self_update_worker = _SelfUpdateWorker(asset, sha256, self, asset_urls=asset_urls)

        def on_prog(frac, stage):
            if hasattr(self, "_update_tip") and self._update_tip:
                self._update_tip.setContent(f"{int(frac * 100)}% {stage}")

        def on_done(ok, msg):
            if hasattr(self, "_update_tip") and self._update_tip:
                self._update_tip.setState(ok)
                self._update_tip = None
            pos = InfoBarPosition.TOP
            if ok:
                InfoBar.success(tr("Update Done Restart", self._lang), "",
                                duration=-1, position=pos, parent=self)
            else:
                InfoBar.error(tr("Update Failed", self._lang), str(msg)[-200:],
                              duration=8000, position=pos, parent=self)

        self._self_update_worker.progress.connect(on_prog)
        self._self_update_worker.finished_ok.connect(on_done)
        self._self_update_worker.start()

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
        elif current is self.quick_page:
            self.quick_page.reload_history()

    def on_lang_changed(self, lang):
        if lang not in UI_LANGS or lang == self._lang:
            return
        self._lang = lang
        backend.set_config("qt_ui_lang", lang)
        # Re-localize each page.
        for page in (self.interface_page, self.translate_page, self.quick_page,
                     self.live_page, self.settings_page, self.history_page,
                     self.proofread_page, self.glossary_page, self.plugins_page):
            if page is not None:   # deferred pages may not be built yet
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
        """Paint a deep-blue (dark) / sky-tinted (light) window surface so the
        desktop app matches the Web UI's identity. Mica is disabled above, so
        this colors both the nav rail and the content area consistently.

        If you ever see a 'light nav + dark content' split on your machine,
        comment out the setCustomBackgroundColor call below to revert to
        plain qfluentwidgets theming (the blue identity still comes from the
        accent color)."""
        try:
            self.setCustomBackgroundColor(QColor(LIGHT_BG), QColor(DARK_BG))
        except Exception:
            pass

    def toggle_theme(self):
        self._theme_dark = not self._theme_dark
        setTheme(Theme.DARK if self._theme_dark else Theme.LIGHT)
        setThemeColor(_accent_for(self._theme_dark))
        self._apply_custom_bg()
        self._sky.set_mode("night" if self._theme_dark else "day")
        backend.set_config("qt_theme", "dark" if self._theme_dark else "light")
        # Repaint the colorful format cards (their tint depends on theme).
        for card in getattr(self.translate_page, "_fmt_cards", []):
            card.refresh_theme()
