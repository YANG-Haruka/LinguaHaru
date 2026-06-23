"""First-run onboarding: interactive spotlight tour (Qt).

Mirrors the Web tour (webapp/static #tour): for each step it navigates to the
relevant nav page, dims the window, cuts a spotlight hole around the active nav
item AND the key control on that page, and floats a callout bubble beside it.
Shown once per install, gated by config key ``onboarding_seen``."""
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QEvent, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QColor, QPen
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    StrongBodyLabel, BodyLabel, PrimaryPushButton, PushButton,
    TransparentPushButton, themeColor,
)

from core import backend
from qt_app.i18n import tr

# (page attr on the main window, resolver -> key widget, title-key, body-key).
# Resolver is wrapped in try/except; a missing widget falls back to the nav item.
_STEPS = [
    ("interface_page", lambda w: w.interface_page.add_btn,    "Onboarding T1 Title", "Onboarding T1 Body"),
    ("quick_page",     lambda w: w.quick_page.input_text,     "Onboarding T2 Title", "Onboarding T2 Body"),
    ("translate_page", lambda w: w.translate_page.dropzone,   "Onboarding T3 Title", "Onboarding T3 Body"),
    ("live_page",      lambda w: w.live_page.go_btn,          "Onboarding T4 Title", "Onboarding T4 Body"),
    ("glossary_page",  lambda w: w.glossary_page.combo,       "Onboarding T5 Title", "Onboarding T5 Body"),
    ("plugins_page",   lambda w: (w.plugins_page._opt_cards[0]
                                  if getattr(w.plugins_page, "_opt_cards", None)
                                  else w.plugins_page),        "Onboarding T6 Title", "Onboarding T6 Body"),
    ("settings_page",  lambda w: w.settings_page.mode_combo,  "Onboarding T7 Title", "Onboarding T7 Body"),
]


class _Callout(QFrame):
    skip = Signal()
    back = Signal()
    nxt = Signal()

    def __init__(self, parent, lang):
        super().__init__(parent)
        self.setObjectName("tourCallout")
        self.setFixedWidth(340)
        self.setStyleSheet(
            "#tourCallout{background:palette(base);border:1px solid rgba(127,127,127,.35);"
            "border-radius:12px;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        self.title = StrongBodyLabel("")
        self.title.setWordWrap(True)
        self.body = BodyLabel("")
        self.body.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        # Progress dots on their OWN line so the button row gets the full width —
        # otherwise dots + 3 buttons crammed in one row clipped the primary
        # button's text on the last step ("开始使用" showed "开"/"用" half-cut).
        self.dots = QLabel("")
        self.dots.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.dots)
        foot = QHBoxLayout()
        foot.setSpacing(6)
        foot.addStretch(1)
        self._skip = TransparentPushButton(tr("Onboarding Skip", lang))
        self._back = PushButton(tr("Onboarding Back", lang))
        self._next = PrimaryPushButton(tr("Onboarding Next", lang))
        for b in (self._skip, self._back, self._next):
            b.setMinimumWidth(b.sizeHint().width())   # never shrink below its text
            foot.addWidget(b)
        self._skip.clicked.connect(self.skip)
        self._back.clicked.connect(self.back)
        self._next.clicked.connect(self.nxt)
        lay.addLayout(foot)


class TourOverlay(QWidget):
    def __init__(self, window, lang):
        super().__init__(window)
        self._win = window
        self._lang = lang
        self._i = 0
        self._target = QRect()
        self._nav = QRect()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(window.rect())
        self._callout = _Callout(self, lang)
        self._callout.skip.connect(self.finish)
        self._callout.back.connect(self._go_back)
        self._callout.nxt.connect(self._go_next)
        window.installEventFilter(self)
        self.raise_()
        self.show()
        self._render()

    # --- geometry helpers ---------------------------------------------------
    def _rect_of(self, widget):
        if widget is None or not widget.isVisible():
            return QRect()
        tl = self.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
        return QRect(tl, widget.size())

    def _nav_rect(self, page):
        try:
            w = self._win.navigationInterface.widget(page.objectName())
        except Exception:  # noqa: BLE001
            w = None
        return self._rect_of(w)

    # --- step flow ----------------------------------------------------------
    def _render(self):
        attr, resolver, tkey, bkey = _STEPS[self._i]
        page = getattr(self._win, attr, None)
        if page is not None:
            self._win.switchTo(page)
        self._callout.title.setText(tr(tkey, self._lang))
        self._callout.body.setText(tr(bkey, self._lang))
        self._callout._back.setVisible(self._i > 0)
        last = self._i == len(_STEPS) - 1
        self._callout._next.setText(
            tr("Onboarding Done" if last else "Onboarding Next", self._lang))
        # The last step's label ("开始使用") is wider than "下一步" — grow the
        # button to its new text so it isn't clipped.
        self._callout._next.setMinimumWidth(self._callout._next.sizeHint().width())
        self._callout.dots.setText("  ".join(
            "●" if i == self._i else "○" for i in range(len(_STEPS))))
        # Locate AFTER the page-switch animation so geometry is final.
        QTimer.singleShot(260, self._relocate)

    def _relocate(self):
        attr, resolver, _t, _b = _STEPS[self._i]
        page = getattr(self._win, attr, None)
        try:
            tw = resolver(self._win)
        except Exception:  # noqa: BLE001
            tw = None
        self._target = self._rect_of(tw)
        self._nav = self._nav_rect(page) if page is not None else QRect()
        if self._target.isNull():            # control hidden -> spotlight nav item only
            self._target = self._nav
        self._place_callout()
        self.update()

    def _place_callout(self):
        self._callout.adjustSize()
        cw, ch = self._callout.width(), self._callout.height()
        r = self._target if not self._target.isNull() else self.rect()
        gap, m = 18, 8
        W, H = self.width(), self.height()
        if r.right() + gap + cw <= W:                 # right
            x, y = r.right() + gap, r.top()
        elif r.left() - gap - cw >= 0:                # left
            x, y = r.left() - gap - cw, r.top()
        elif r.bottom() + gap + ch <= H:              # below
            x, y = r.left(), r.bottom() + gap
        else:                                          # above
            x, y = r.left(), r.top() - gap - ch
        x = max(m, min(x, W - cw - m))
        y = max(m, min(y, H - ch - m))
        self._callout.move(x, y)

    def _go_back(self):
        if self._i > 0:
            self._i -= 1
            self._render()

    def _go_next(self):
        if self._i < len(_STEPS) - 1:
            self._i += 1
            self._render()
        else:
            self.finish()

    def finish(self):
        try:
            self._win.removeEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        backend.set_config("onboarding_seen", True)
        self.hide()
        self.deleteLater()

    # --- painting + events --------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        for r in (self._target, self._nav):
            if not r.isNull():
                hole = QPainterPath()
                hole.addRoundedRect(QRectF(r.adjusted(-6, -6, 6, 6)), 10, 10)
                path = path.subtracted(hole)
        p.fillPath(path, QColor(0, 0, 0, 150))
        pen = QPen(themeColor(), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for r in (self._target, self._nav):
            if not r.isNull():
                p.drawRoundedRect(r.adjusted(-6, -6, 6, 6), 10, 10)

    def mousePressEvent(self, ev):
        ev.accept()                     # swallow clicks on the dimmed page

    def eventFilter(self, obj, ev):
        if obj is self._win and ev.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self.setGeometry(self._win.rect())
            self._relocate()
        return False


def maybe_show_onboarding(parent, lang):
    """Show the spotlight tour once per install. No-op if already seen.
    Returns the overlay (kept alive by its parent) or None."""
    if backend.get_config("onboarding_seen", False):
        return None
    return TourOverlay(parent, lang)
