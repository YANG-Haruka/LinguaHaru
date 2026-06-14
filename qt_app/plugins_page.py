"""Plugins page: 'every format is a plugin'.

Two sections:
  - Built-in formats: always-present format plugins (docx, pptx, ... json),
    shown as enabled with no action.
  - Optional plugins: PDF (BabelDOC), Image OCR (PP-OCRv6/PaddleOCR) and
    Video/Audio (faster-whisper + ffmpeg). Live status from
    core.optional_modules.module_status(); an Install button runs the matching
    `pip install -r requirements-*.txt` in an InstallWorker and re-checks
    availability on finish.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, CaptionLabel, StrongBodyLabel,
    SimpleCardWidget, CardWidget, IconWidget, FluentIcon, FlowLayout,
    PushButton, InfoBadge, InfoBar, InfoBarPosition,
)

from qt_app.i18n import tr
from qt_app.worker import InstallWorker, ModuleUpdateCheckWorker

# Built-in format plugins (always available). (label, FluentIcon)
_BUILTIN_FORMATS = [
    ("docx", FluentIcon.DOCUMENT), ("pptx", FluentIcon.LAYOUT),
    ("xlsx", FluentIcon.TILES), ("txt", FluentIcon.FONT),
    ("md", FluentIcon.CODE), ("srt", FluentIcon.MOVIE),
    ("vtt", FluentIcon.MOVIE), ("ass", FluentIcon.MOVIE),
    ("lrc", FluentIcon.ALBUM), ("epub", FluentIcon.LIBRARY),
    ("csv", FluentIcon.DICTIONARY), ("tsv", FluentIcon.DICTIONARY),
    ("html", FluentIcon.GLOBE), ("odt", FluentIcon.DOCUMENT),
    ("json", FluentIcon.CODE),
]

_OPTIONAL_ICONS = {
    "PDF": FluentIcon.DOCUMENT,
    "Image OCR": FluentIcon.PHOTO,
    "Video/Audio": FluentIcon.MOVIE,
}


class _BuiltinChip(SimpleCardWidget):
    """A small enabled-format chip."""

    def __init__(self, label, icon, lang="en", parent=None):
        super().__init__(parent)
        self.setFixedSize(132, 56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(22, 22)
        layout.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(StrongBodyLabel(label, self))
        col.addWidget(CaptionLabel(tr("Built-in", lang), self))
        layout.addLayout(col, 1)


class OptionalPluginCard(CardWidget):
    """A downloadable optional plugin (PDF / Image OCR / Video)."""

    def __init__(self, mod, lang="en", on_install=None, parent=None):
        super().__init__(parent)
        self._mod = mod
        self._lang = lang
        self._on_install = on_install
        self._upgrade_info = None  # (current, latest) when an upgrade is offered
        # Fixed width so exactly three cards fit per row (FlowLayout sizes to
        # content otherwise, which only fit two).
        self.setFixedWidth(258)
        self.setFixedHeight(166)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon = IconWidget(_OPTIONAL_ICONS.get(mod["name"], FluentIcon.APPLICATION), self)
        self.icon.setFixedSize(28, 28)
        head.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(StrongBodyLabel(mod["name"], self))
        col.addWidget(CaptionLabel(mod["detail"], self))
        head.addLayout(col, 1)
        self.badge = None
        head.addStretch(1)
        layout.addLayout(head)

        self.cmd_label = CaptionLabel(mod["install"], self)
        self.cmd_label.setWordWrap(True)
        layout.addWidget(self.cmd_label)

        if mod["name"] == "Video/Audio":
            note = CaptionLabel(tr("Video ffmpeg note", lang), self)
            note.setWordWrap(True)
            layout.addWidget(note)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        # Shown only when PyPI reports a newer version of an installed plugin.
        self.upgrade_btn = PushButton(FluentIcon.UPDATE, tr("Upgrade", lang), self)
        self.upgrade_btn.clicked.connect(self._clicked_upgrade)
        self.upgrade_btn.hide()
        btn_row.addWidget(self.upgrade_btn)
        self.install_btn = PushButton(FluentIcon.DOWNLOAD, tr("Install", lang), self)
        self.install_btn.clicked.connect(self._clicked)
        btn_row.addWidget(self.install_btn)
        layout.addLayout(btn_row)

        self.set_state(mod["available"])

    def _clicked(self):
        if callable(self._on_install):
            action = "uninstall" if self._mod["available"] else "install"
            self._on_install(self, action)

    def _clicked_upgrade(self):
        if callable(self._on_install):
            self._on_install(self, "upgrade")

    def show_upgrade(self, current, latest):
        """Reveal the Upgrade button with the available version transition."""
        self._upgrade_info = (current, latest)
        self.upgrade_btn.setText(f"{tr('Upgrade', self._lang)} ({current} → {latest})")
        self.upgrade_btn.show()

    def hide_upgrade(self):
        self._upgrade_info = None
        self.upgrade_btn.hide()

    def set_state(self, available, busy=False):
        if self.badge is not None:
            self.badge.deleteLater()
            self.badge = None
        if busy:
            self.install_btn.setEnabled(False)
            self.install_btn.setText(tr("Working", self._lang) + "…")
            self.upgrade_btn.setEnabled(False)
            return
        self.upgrade_btn.setEnabled(True)
        if available:
            self.badge = InfoBadge.success(tr("Installed", self._lang), self)
            self.layout().itemAt(0).layout().addWidget(self.badge)
            self.install_btn.setEnabled(True)
            self.install_btn.setText(tr("Uninstall", self._lang))
        else:
            self.hide_upgrade()  # nothing to upgrade once removed
            self.install_btn.setEnabled(True)
            self.install_btn.setText(tr("Install", self._lang))


class PluginsPage(ScrollArea):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("PluginsPage")
        self._lang = lang
        self._worker = None
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        container = QWidget()
        container.setObjectName("pluginsScrollContainer")
        container.setStyleSheet(
            "#pluginsScrollContainer { background-color: transparent; }")
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(16)

        self.title = TitleLabel(tr("Plugins", lang))
        layout.addWidget(self.title)
        self.subtitle = CaptionLabel(tr("Every format is a plugin", lang))
        layout.addWidget(self.subtitle)

        # --- Optional plugins (the centerpiece) ---
        self.opt_header = StrongBodyLabel(tr("Optional Plugins", lang))
        layout.addWidget(self.opt_header)
        opt_card = SimpleCardWidget()
        opt_layout = QVBoxLayout(opt_card)
        opt_layout.setContentsMargins(20, 16, 20, 16)
        opt_layout.setSpacing(12)
        opt_flow_host = QWidget()
        self.opt_flow = FlowLayout(opt_flow_host, needAni=False)
        self.opt_flow.setHorizontalSpacing(14)
        self.opt_flow.setVerticalSpacing(14)
        self._opt_cards = []
        self._check_workers = []
        for mod in backend_module_status():
            card = OptionalPluginCard(mod, lang, on_install=self._start_install)
            self._opt_cards.append(card)
            self.opt_flow.addWidget(card)
            if mod["available"]:
                self._start_update_check(card)
        opt_layout.addWidget(opt_flow_host)
        layout.addWidget(opt_card)

        # --- Built-in formats ---
        self.builtin_header = StrongBodyLabel(tr("Built-in Formats", lang))
        layout.addWidget(self.builtin_header)
        builtin_card = SimpleCardWidget()
        b_layout = QVBoxLayout(builtin_card)
        b_layout.setContentsMargins(20, 16, 20, 16)
        b_flow_host = QWidget()
        b_flow = FlowLayout(b_flow_host, needAni=False)
        b_flow.setHorizontalSpacing(10)
        b_flow.setVerticalSpacing(10)
        for label, icon in _BUILTIN_FORMATS:
            b_flow.addWidget(_BuiltinChip(label, icon, lang))
        b_layout.addWidget(b_flow_host)
        layout.addWidget(builtin_card)

        layout.addStretch(1)

    def retranslate(self, lang):
        self._lang = lang
        self.title.setText(tr("Plugins", lang))
        self.subtitle.setText(tr("Every format is a plugin", lang))
        self.opt_header.setText(tr("Optional Plugins", lang))
        self.builtin_header.setText(tr("Built-in Formats", lang))
        for card in self._opt_cards:
            card._lang = lang
            card.set_state(card._mod["available"])
            if card._upgrade_info:  # set_state doesn't touch a visible upgrade btn
                card.show_upgrade(*card._upgrade_info)

    def _start_update_check(self, card):
        """Ask PyPI (off the UI thread) whether this installed plugin has a
        newer version, and reveal the Upgrade button if so."""
        worker = ModuleUpdateCheckWorker(card._mod["name"])
        self._check_workers.append(worker)
        worker.result.connect(lambda name, info, c=card: self._update_check_done(c, info))
        worker.start()

    def _update_check_done(self, card, info):
        if info.get("update"):
            card.show_upgrade(info.get("current", "?"), info.get("latest", "?"))

    def _start_install(self, card, action="install"):
        if self._worker is not None and self._worker.isRunning():
            return
        card.set_state(card._mod["available"], busy=True)
        verbs = {"uninstall": tr("Uninstalling", self._lang),
                 "upgrade": tr("Upgrading", self._lang),
                 "install": tr("Installing", self._lang)}
        verb = verbs.get(action, verbs["install"])
        self._info(card._mod["name"], verb + " " + card._mod["name"])
        worker = InstallWorker(card._mod["name"], action=action)
        self._worker = worker
        worker.line.connect(lambda text: card.cmd_label.setText(text[-90:]))
        worker.finished_ok.connect(lambda ok, msg, c=card, a=action: self._install_done(c, ok, msg, a))
        worker.start()

    def _install_done(self, card, ok, msg, action="install"):
        # Re-probe availability so the card reflects reality.
        from importlib import reload
        import core.optional_modules as om
        reload(om)
        new_status = {m["name"]: m for m in om.module_status()}
        mod = new_status.get(card._mod["name"], card._mod)
        card._mod = mod
        card.cmd_label.setText(mod["install"])
        card.set_state(mod["available"])
        if ok and mod["available"]:
            if action == "upgrade":
                card.hide_upgrade()  # now on the latest version
            finished = tr("Upgrade finished", self._lang) if action == "upgrade" \
                else tr("Install finished", self._lang)
            self._info(card._mod["name"], finished)
        else:
            self._info(card._mod["name"],
                       f"{tr('Install failed', self._lang)}: {msg}", error=True)
        self._worker = None

    def _info(self, title, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)


def backend_module_status():
    """Indirection so tests can monkeypatch easily; mirrors module_status()."""
    from core.optional_modules import module_status
    return module_status()
