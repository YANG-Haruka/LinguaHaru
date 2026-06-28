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

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
)

from qfluentwidgets import (
    ScrollArea, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel,
    SimpleCardWidget, CardWidget, IconWidget, FluentIcon, FlowLayout,
    PushButton, PrimaryPushButton, HyperlinkButton, InfoBadge, InfoBar, InfoBarPosition,
    MessageBox, MessageBoxBase,
)


class _MarketFetchWorker(QThread):
    """Fetch the remote plugin index off the UI thread."""
    done = Signal(list)

    def run(self):
        try:
            from core import plugins_registry
            self.done.emit(plugins_registry.remote_available())
        except Exception:  # noqa: BLE001
            self.done.emit([])


class _MarketDownloadWorker(QThread):
    """Download a self-contained plugin from the market."""
    done = Signal(bool, str)

    def __init__(self, key, url, parent=None):
        super().__init__(parent)
        self._key, self._url = key, url

    def run(self):
        try:
            from core import plugins_registry
            ok, msg = plugins_registry.download_remote_plugin(self._key, self._url)
            self.done.emit(ok, msg)
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))

from qt_app.i18n import tr
from qt_app.worker import (
    InstallWorker, ModuleUpdateCheckWorker, ModelDownloadWorker,
    ModelDeleteWorker, PluginSpaceWorker,
)

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
    "漫画翻译": FluentIcon.ALBUM,
    "Video/Audio": FluentIcon.MOVIE,
    "Real-Time Voice": FluentIcon.MICROPHONE,
}

# Uniform card geometry — every optional-plugin card is exactly this size so
# the FlowLayout renders a tidy aligned grid (3 per row). The height reserves
# room for the (optional) model line + download status so model and non-model
# cards line up.
CARD_WIDTH = 250
CARD_HEIGHT = 198   # room for the model line + a one-line disk-usage line


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


class _ModelPickerDialog(MessageBoxBase):
    """Per-model management for a plugin: each model shows its install status +
    disk size with an Install / Delete button (white row = installed → Delete,
    gray = not installed → Install). Clicking an installed model makes it the
    active (in-use) one. Acts live (its own workers) and refreshes in place."""

    def __init__(self, plugin, lang="en", parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._lang = lang
        self._workers = []
        self.titleLabel = StrongBodyLabel(tr("Select Model", lang), self)
        self.viewLayout.addWidget(self.titleLabel)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        self.viewLayout.addLayout(self._rows)
        self.yesButton.setText(tr("Close", lang))
        self.cancelButton.hide()
        self.widget.setMinimumWidth(460)
        self._build()

    @staticmethod
    def _short(label):
        for ch in ("（", "("):
            i = (label or "").find(ch)
            if i > 0:
                return label[:i].strip()
        return label

    def _build(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        from core.optional_modules import plugin_model_states, plugin_current_model
        self._cur = plugin_current_model(self._plugin)
        for st in plugin_model_states(self._plugin, with_size=True):
            self._rows.addWidget(self._row(st))

    def _row(self, st):
        row = QWidget()
        row.setObjectName("pickRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(8)
        h.addWidget(StrongBodyLabel(self._short(st["label"])))
        h.addStretch(1)
        sz = CaptionLabel(st.get("disk_human", "") if st["downloaded"]
                          else tr("Not Installed", self._lang))
        sz.setTextColor("#808080", "#a0a0a0")
        h.addWidget(sz)
        if st["downloaded"]:
            b = PushButton(tr("Delete", self._lang))
            b.clicked.connect(lambda _=False, s=st: self._delete(s))
        else:
            b = PushButton(tr("Install", self._lang))
            b.clicked.connect(lambda _=False, s=st: self._install(s))
        h.addWidget(b)
        active = st["id"] == self._cur
        border = ("rgba(10,132,255,0.85)" if active else "rgba(128,128,128,0.28)")
        row.setStyleSheet(f"#pickRow{{border:1px solid {border};border-radius:8px;}}")
        if st["downloaded"] and not active:
            row.setCursor(Qt.PointingHandCursor)
            row.mousePressEvent = lambda _e, s=st: self._activate(s)
        return row

    def _activate(self, st):
        from core.optional_modules import set_plugin_model
        set_plugin_model(self._plugin, st["id"])
        self._build()

    def _install(self, st):
        w = ModelDownloadWorker(self._plugin, st["id"], self)
        self._workers.append(w)
        w.progress.connect(lambda f, s: self.titleLabel.setText(
            tr("Downloading Model", self._lang) + f" {int(max(0.0, min(1.0, f)) * 100)}%"))
        w.finished_ok.connect(lambda ok: (
            self.titleLabel.setText(tr("Select Model", self._lang) if ok
                                    else tr("Download Failed", self._lang)),
            self._build()))
        w.start()
        self._build()   # reflect "downloading" by re-reading state shortly

    def _delete(self, st):
        box = MessageBox(tr("Delete", self._lang),
                         tr("Delete Model Confirm", self._lang), self.window())
        if not box.exec():
            return
        w = ModelDeleteWorker(self._plugin, st["id"], self)
        self._workers.append(w)
        w.finished_ok.connect(lambda _ok: self._build())
        w.start()


class OptionalPluginCard(CardWidget):
    """A downloadable optional plugin (PDF / Image OCR / Video)."""

    def __init__(self, mod, lang="en", on_install=None, on_select_model=None, parent=None):
        super().__init__(parent)
        self._mod = mod
        self._lang = lang
        self._on_install = on_install
        self._on_select_model = on_select_model
        self._upgrade_info = None  # (current, latest) when an upgrade is offered
        self.model_link = None     # clickable "Model: <label>" line (model plugins)
        self.status_caption = None  # transient download status under the model line
        self._models = mod.get("models") or []
        self._busy_download = False
        # Uniform geometry: every card is the SAME fixed width and height so the
        # FlowLayout lays them out as a tidy aligned grid (3 per row). PDF (no
        # model) and model cards look identical; the model line slot is reserved
        # on every card so heights match regardless of content.
        self.setFixedWidth(CARD_WIDTH)
        self.setFixedHeight(CARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.icon = IconWidget(_OPTIONAL_ICONS.get(mod["name"], FluentIcon.APPLICATION), self)
        self.icon.setFixedSize(26, 26)
        head.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(StrongBodyLabel(mod["name"], self))
        head.addLayout(col, 1)
        self.badge = None
        head.addStretch(1)
        layout.addLayout(head)

        # Short engine subtitle (muted) — e.g. "BabelDOC" / "RapidOCR · …".
        # The raw "pip install …" command is NOT shown here; it lives on the
        # install button's tooltip instead.
        self.subtitle = CaptionLabel(mod["detail"], self)
        self.subtitle.setWordWrap(True)
        self.subtitle.setTextColor("#808080", "#a0a0a0")
        layout.addWidget(self.subtitle)

        layout.addStretch(1)

        # Model line (line 1): a clickable link for plugins with a selectable
        # model, a plain (normal-color) label for a fixed model like PDF's
        # DocLayout — same slot/position on every card.
        if self._models:
            self.model_link = HyperlinkButton("", "", self)
            self.model_link.setText(self._current_model_text())
            self.model_link.clicked.connect(self._open_model_picker)
            layout.addWidget(self.model_link, 0, Qt.AlignLeft)
        elif self._mod.get("fixed_model"):
            layout.addWidget(
                BodyLabel(f"{tr('Model', lang)}: {self._mod['fixed_model']}", self),
                0, Qt.AlignLeft)
        # Usage line (line 2, every card): downloaded models + disk space, so the
        # user can manage space. Overwritten with the download status while a
        # model is downloading, then refreshed.
        self.status_caption = CaptionLabel("", self)
        self.status_caption.setTextColor("#808080", "#a0a0a0")
        layout.addWidget(self.status_caption)
        self._refresh_usage()

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        # Shown only when PyPI reports a newer version of an installed plugin.
        self.upgrade_btn = PushButton(FluentIcon.UPDATE, tr("Upgrade", lang), self)
        self.upgrade_btn.clicked.connect(self._clicked_upgrade)
        self.upgrade_btn.hide()
        btn_row.addWidget(self.upgrade_btn)
        self.install_btn = PushButton(FluentIcon.DOWNLOAD, tr("Install", lang), self)
        self.install_btn.setToolTip(mod["install"])  # raw pip command lives here
        self.install_btn.clicked.connect(self._clicked)
        btn_row.addWidget(self.install_btn)
        layout.addLayout(btn_row)

        self.set_state(mod["available"])

    def _clicked(self):
        if callable(self._on_install):
            # A reuses card (漫画翻译) only ever installs its shared plugin; its
            # uninstall button is hidden when available, so never uninstall here.
            if self._mod.get("reuses"):
                action = "install"
            else:
                action = "uninstall" if self._mod["available"] else "install"
            self._on_install(self, action)

    def _clicked_upgrade(self):
        if callable(self._on_install):
            self._on_install(self, "upgrade")

    def _current_model_text(self):
        """The model line label: 'Model: <short label>'. The "(...)" detail is
        stripped for this compact line; the full label shows in the picker."""
        cur = self._mod.get("current_model")
        label = cur
        for m in self._models:
            if m.get("id") == cur:
                label = m.get("label", cur)
                break
        for ch in ("（", "("):
            idx = (label or "").find(ch)
            if idx > 0:
                label = label[:idx].strip()
                break
        return f"{tr('Model', self._lang)}: {label}"

    @staticmethod
    def _short_label(label):
        for ch in ("（", "("):
            idx = (label or "").find(ch)
            if idx > 0:
                return label[:idx].strip()
        return label

    def _refresh_usage(self):
        """Fill the usage line: library (pip deps) + model disk volumes, so the
        user can manage space. Computed in a background thread — the pip-deps
        stat-walk is slow the first time."""
        if self.status_caption is None:
            return
        self.status_caption.setText(tr("Calculating", self._lang) + "…")
        w = PluginSpaceWorker(self._mod["name"], self)
        self._space_worker = w
        w.result.connect(self._on_space)
        w.start()

    def _on_space(self, name, space):
        if self.status_caption is None or not space:
            if self.status_caption is not None:
                self.status_caption.setText("")
            return
        shared = (f"（{tr('Shared', self._lang)}）"
                  if space.get("shared") and space.get("model_bytes") else "")
        self.status_caption.setText(
            f"{tr('Library Size', self._lang)} {space['lib_human']} · "
            f"{tr('Models Size', self._lang)} {space['model_human']}{shared}")

    def _open_model_picker(self):
        """Open the per-model management dialog (install/delete + size + active).
        The dialog acts live; afterwards refresh the card's model line + usage."""
        if self._busy_download:
            return
        dlg = _ModelPickerDialog(self._mod["name"], self._lang, parent=self.window())
        dlg.exec()
        # Reflect any change (active model, install/delete) on the card.
        from core.optional_modules import plugin_current_model
        self._mod["current_model"] = plugin_current_model(self._mod["name"])
        if self.model_link is not None:
            self.model_link.setText(self._current_model_text())
        self._refresh_usage()

    def set_download_busy(self, busy):
        """Show a 'downloading' status under the model line and lock the line."""
        if self.model_link is None:
            return
        self._busy_download = busy
        self.model_link.setEnabled(not busy)
        if self.status_caption is not None:
            self.status_caption.setText(
                tr("Downloading Model", self._lang) if busy else "")

    def set_install_line(self, text):
        """Show a short text status on the visible usage line (immediate feedback /
        restart hint) — NOT raw log lines."""
        if self.status_caption is not None:
            self.status_caption.setText(text[-60:])

    def set_progress(self, frac, stage):
        """Show a PERCENTAGE (not log lines) on the visible usage line, with a
        verb chosen from the stage: 下载中 for the model phase, else 安装中."""
        if self.status_caption is None:
            return
        verb = (tr("Downloading Model", self._lang) if stage == "downloading"
                else tr("Installing", self._lang))
        self.status_caption.setText(f"{verb} {int(max(0.0, min(1.0, frac)) * 100)}%")

    def set_model_ready(self, ok):
        if self.model_link is not None:
            self.model_link.setEnabled(True)
            self.model_link.setText(self._current_model_text())
        self._busy_download = False
        # Refresh the usage line so the newly-downloaded model's size shows.
        self._refresh_usage()

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
        reuses = self._mod.get("reuses")
        if available:
            self.badge = InfoBadge.success(tr("Installed", self._lang), self)
            self.layout().itemAt(0).layout().addWidget(self.badge)
            # A reuses card (漫画翻译) shares another plugin's deps — there's nothing
            # to uninstall separately, so hide the button once it's available.
            self.install_btn.setVisible(not reuses)
            self.install_btn.setEnabled(True)
            self.install_btn.setText(tr("Uninstall", self._lang))
        else:
            self.hide_upgrade()  # nothing to upgrade once removed
            self.install_btn.setVisible(True)
            self.install_btn.setEnabled(True)
            self.install_btn.setText(tr("Install", self._lang))


class PluginsPage(ScrollArea):
    def __init__(self, parent=None, lang="en"):
        super().__init__(parent)
        self.setObjectName("PluginsPage")
        self._lang = lang
        self._worker = None
        self._install_queue = []   # [(card, action)] pending while a job runs
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
        self._dl_workers = []
        for mod in backend_module_status():
            card = OptionalPluginCard(
                mod, lang, on_install=self._start_install,
                on_select_model=self._start_model_download)
            self._opt_cards.append(card)
            self.opt_flow.addWidget(card)
            if mod["available"]:
                self._start_update_check(card)
        opt_layout.addWidget(opt_flow_host)
        layout.addWidget(opt_card)

        # --- Plugin market (downloadable plugins, fetched on demand) ---
        mkt_head = QHBoxLayout()
        self.market_header = StrongBodyLabel(tr("Plugin Market", lang))
        mkt_head.addWidget(self.market_header)
        mkt_head.addStretch(1)
        self.market_refresh = PushButton(FluentIcon.SYNC, tr("Refresh Market", lang))
        self.market_refresh.clicked.connect(self._refresh_market)
        mkt_head.addWidget(self.market_refresh)
        layout.addLayout(mkt_head)
        market_card = SimpleCardWidget()
        m_layout = QVBoxLayout(market_card)
        m_layout.setContentsMargins(20, 16, 20, 16)
        self.market_hint = CaptionLabel(tr("Refresh Market", lang))
        m_layout.addWidget(self.market_hint)
        m_flow_host = QWidget()
        self.market_flow = FlowLayout(m_flow_host, needAni=False)
        self.market_flow.setHorizontalSpacing(14)
        self.market_flow.setVerticalSpacing(14)
        m_layout.addWidget(m_flow_host)
        layout.addWidget(market_card)
        self._market_cards = []
        self._market_fetch = None
        self._market_dl = None

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
        self.market_header.setText(tr("Plugin Market", lang))
        self.market_refresh.setText(tr("Refresh Market", lang))
        for card in self._opt_cards:
            card._lang = lang
            card.set_state(card._mod["available"])
            if card._upgrade_info:  # set_state doesn't touch a visible upgrade btn
                card.show_upgrade(*card._upgrade_info)
            if card.model_link is not None:
                card.model_link.setText(card._current_model_text())

    def shutdown(self):
        """Wait for every background worker (disk-size probe, update check,
        install, model download/delete) so closing the app doesn't destroy a
        thread mid-run ('QThread: Destroyed while thread is still running')."""
        workers = list(self._check_workers) + list(self._dl_workers)
        if self._worker is not None:
            workers.append(self._worker)
        for w in (getattr(self, "_market_fetch", None), getattr(self, "_market_dl", None)):
            if w is not None:
                workers.append(w)
        for card in self._opt_cards:
            workers.extend(getattr(card, "_workers", []) or [])
            sw = getattr(card, "_space_worker", None)
            if sw is not None:
                workers.append(sw)
        for w in workers:
            try:
                if w is not None and w.isRunning():
                    w.requestInterruption()
                    w.wait(3000)
            except RuntimeError:
                pass   # C++ object already deleted

    # --- Plugin market ---
    def _refresh_market(self):
        self.market_refresh.setEnabled(False)
        self.market_hint.setText(tr("Downloading", self._lang))
        self._market_fetch = _MarketFetchWorker(self)
        self._market_fetch.done.connect(self._populate_market)
        self._market_fetch.start()

    def _populate_market(self, plugins):
        self.market_refresh.setEnabled(True)
        for c in self._market_cards:
            self.market_flow.removeWidget(c)
            c.deleteLater()
        self._market_cards = []
        if not plugins:
            self.market_hint.setText(tr("Plugin Market", self._lang) + " — 0")
            return
        self.market_hint.setText("")
        for p in plugins:
            card = self._market_card(p)
            self._market_cards.append(card)
            self.market_flow.addWidget(card)

    def _market_card(self, p):
        card = SimpleCardWidget()
        card.setFixedWidth(CARD_WIDTH)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)
        lay.addWidget(StrongBodyLabel(p.get("name", p["key"]), card))
        det = CaptionLabel((p.get("detail", "") + (f" · v{p['version']}" if p.get("version") else "")), card)
        det.setWordWrap(True)
        det.setTextColor("#808080", "#a0a0a0")
        lay.addWidget(det)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = PrimaryPushButton(FluentIcon.DOWNLOAD, tr("Download", self._lang), card)
        btn.clicked.connect(lambda _=False, pl=p, b=btn: self._download_market(pl, b))
        row.addWidget(btn)
        lay.addLayout(row)
        return card

    def _download_market(self, p, btn):
        btn.setEnabled(False)
        self._market_dl = _MarketDownloadWorker(p["key"], p["url"], self)

        def done(ok, msg):
            if ok:
                # Downloaded plugins need a restart to activate their entry hook +
                # appear in Optional Plugins (where deps are installed).
                InfoBar.success(tr("Download", self._lang),
                                tr("Update Done Restart", self._lang),
                                duration=-1, position=InfoBarPosition.TOP, parent=self)
                for c in self._market_cards:
                    if c is btn.parent():
                        self.market_flow.removeWidget(c)
                        c.deleteLater()
                        self._market_cards.remove(c)
                        break
            else:
                btn.setEnabled(True)
                InfoBar.error(tr("Update Failed", self._lang), str(msg)[-200:],
                              duration=6000, position=InfoBarPosition.TOP, parent=self)
        self._market_dl.done.connect(done)
        self._market_dl.start()

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

    def _start_model_download(self, card, model_id=None):
        """Download a plugin model off the UI thread. model_id=None downloads the
        plugin's current/default model (used right after a fresh install)."""
        if model_id:
            # ModelDownloadWorker persists the id; reflect it locally so the
            # model line shows the new selection once the download finishes.
            card._mod["current_model"] = model_id
        card.set_download_busy(True)
        worker = ModelDownloadWorker(card._mod["name"], model_id)
        self._dl_workers.append(worker)
        worker.progress.connect(lambda f, s, c=card: c.set_progress(f, s))
        worker.finished_ok.connect(
            lambda ok, c=card, w=worker: self._model_download_done(c, ok, w))
        worker.start()

    def _model_download_done(self, card, ok, worker):
        card.set_model_ready(ok)
        if not ok:
            self._info(card._mod["name"],
                       tr("Model Download Failed Hint", self._lang), error=True)
        if worker in self._dl_workers:
            self._dl_workers.remove(worker)

    def _drain_install_queue(self):
        """Current job done — clear it and start the next queued plugin (one job
        at a time, so concurrent pip/uv calls can't corrupt the shared env)."""
        self._worker = None
        if self._install_queue:
            card, action = self._install_queue.pop(0)
            self._begin_install(card, action)

    def _start_install(self, card, action="install"):
        if action == "uninstall":
            # Confirm before removing (do it now, not when dequeued); shared
            # models are kept.
            box = MessageBox(tr("Uninstall", self._lang),
                             tr("Uninstall Models Confirm", self._lang), self.window())
            box.yesButton.setText(tr("Confirm", self._lang))
            box.cancelButton.setText(tr("Cancel", self._lang))
            if not box.exec():
                return
        # Plugin jobs mutate the SAME interpreter env, so they MUST run one at a
        # time. If one is running, queue this one and show "排队中"; it starts
        # automatically when the current job finishes.
        if self._worker is not None and self._worker.isRunning():
            if not any(c is card for c, _ in self._install_queue):
                self._install_queue.append((card, action))
                card.set_state(card._mod["available"], busy=True)
                card.set_install_line(tr("Status Queued", self._lang))
            return
        self._begin_install(card, action)

    def _begin_install(self, card, action="install"):
        card.set_state(card._mod["available"], busy=True)
        verbs = {"uninstall": tr("Uninstalling", self._lang),
                 "upgrade": tr("Upgrading", self._lang),
                 "install": tr("Installing", self._lang)}
        verb = verbs.get(action, verbs["install"])
        self._info(card._mod["name"], verb + " " + card._mod["name"])
        card.set_install_line(verb + "…")   # immediate visible feedback
        # reuses cards (漫画翻译) install/uninstall their shared plugin (Image OCR).
        target = card._mod.get("reuses") or card._mod["name"]
        worker = InstallWorker(target, action=action)
        self._worker = worker
        # Show a visible PERCENTAGE on the card (not log lines), so the user can
        # see it progress instead of a wall of pip output.
        worker.progress.connect(lambda f, s, c=card: c.set_progress(f, s))
        worker.finished_ok.connect(
            lambda ok, msg, c=card, a=action, w=worker: self._install_done(c, ok, msg, a, w))
        worker.start()

    def _install_done(self, card, ok, msg, action="install", worker=None):
        # Re-probe availability so the card reflects reality.
        from importlib import reload
        import core.optional_modules as om
        reload(om)
        new_status = {m["name"]: m for m in om.module_status()}
        mod = new_status.get(card._mod["name"], card._mod)
        card._mod = mod
        card.install_btn.setToolTip(mod["install"])
        if card.model_link is not None:
            card.model_link.setText(card._current_model_text())
        card.set_state(mod["available"])
        card._refresh_usage()   # disk usage changed (model deleted / downloaded)
        from core.log_config import system_event
        from core.model_store import human_size
        if action == "uninstall":
            if not ok:
                # Honor the failure — don't report success when uninstall errored.
                self._info(card._mod["name"], f"{tr('Failed', self._lang)}: {(msg or '')[-200:]}", error=True)
                system_event(f"Plugin uninstall FAILED: {card._mod['name']}")
                self._drain_install_queue()
                return
            # Cleanup report: "Cleanup done, freed N MB" (freed=0 when everything
            # was shared and kept).
            freed = getattr(worker, "freed_bytes", 0) or 0
            note = f"{tr('Cleanup Done', self._lang)}"
            if freed > 0:
                note += f" · {tr('Freed', self._lang)} {human_size(freed)}"
            self._info(card._mod["name"], note)
            system_event(f"Plugin uninstall: {card._mod['name']}"
                         + (f" | freed {human_size(freed)}" if freed else ""))
            self._drain_install_queue()
            return
        if ok and mod["available"]:
            system_event(f"Plugin {action}: {card._mod['name']}")
        if ok and mod["available"]:
            if action == "upgrade":
                card.hide_upgrade()  # now on the latest version
            finished = tr("Upgrade finished", self._lang) if action == "upgrade" \
                else tr("Install finished", self._lang)
            self._info(card._mod["name"], finished)
            # Unified UX: a fresh install auto-downloads the default model.
            if action == "install" and mod.get("models"):
                # Re-trigger via the tracked worker so the card shows progress AND
                # surfaces a network failure (was silent before).
                self._start_model_download(card)
            elif msg == "__MODEL_FAILED__":
                # Fixed-model plugin (e.g. PDF) with no separate picker to retry
                # from — its model download failed, so say so instead of "完成".
                self._info(card._mod["name"],
                           tr("Model Download Failed Hint", self._lang), error=True)
        elif ok:
            # Installed/upgraded OK, but the new package can't be imported in this
            # already-running process yet (heavy deps like torch/funasr) — so the
            # card still reads "not installed". It activates after a restart; say
            # so instead of the misleading "failed".
            card.set_install_line(tr("Restart To Activate", self._lang))
            self._info(card._mod["name"], tr("Restart To Activate", self._lang))
            system_event(f"Plugin {action} (pending restart): {card._mod['name']}")
        else:
            self._info(card._mod["name"],
                       f"{tr('Install failed', self._lang)}: {msg}", error=True)
        self._drain_install_queue()

    def _info(self, title, text, error=False):
        bar = InfoBar.error if error else InfoBar.success
        bar(title, text, orient=1, isClosable=True,
            position=InfoBarPosition.TOP, duration=4000, parent=self)


def backend_module_status():
    """Indirection so tests can monkeypatch easily; mirrors module_status()."""
    from core.optional_modules import module_status
    return module_status()
