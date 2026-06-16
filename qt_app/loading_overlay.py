"""A glass loading lock for the Qt app.

Shown while an STT model loads (first use downloads + warms it for seconds).
It covers its parent window with a translucent, click-blocking backdrop and a
centered spinner + message, so the user sees progress and can't mis-click the
half-ready UI. Mirrors the Web side's #model-loading-overlay.
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame

from qfluentwidgets import IndeterminateProgressRing, BodyLabel, isDarkTheme

from qt_app.i18n import tr


class LoadingOverlay(QWidget):
    """Translucent click-blocking overlay with a spinner + text, sized to fill
    its parent. Call show_with(text) to display, hide() to dismiss."""

    def __init__(self, parent, lang="en"):
        super().__init__(parent)
        self._lang = lang
        self.setAttribute(Qt.WA_StyledBackground, True)
        # A real card so the spinner/text sit on a solid surface above the blur.
        self._card = QFrame(self)
        self._card.setObjectName("loadingCard")
        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(40, 34, 40, 34)
        cl.setSpacing(18)
        self._ring = IndeterminateProgressRing(self._card)
        self._ring.setFixedSize(46, 46)
        cl.addWidget(self._ring, 0, Qt.AlignHCenter)
        self._label = BodyLabel(tr("Loading model", lang), self._card)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        cl.addWidget(self._label, 0, Qt.AlignHCenter)

        lay = QVBoxLayout(self)
        lay.addWidget(self._card, 0, Qt.AlignCenter)
        self._apply_theme()
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    def _apply_theme(self):
        if isDarkTheme():
            card_bg, border, text = "rgba(40,42,48,0.96)", "rgba(255,255,255,0.08)", "#f0f0f0"
        else:
            card_bg, border, text = "rgba(255,255,255,0.97)", "rgba(0,0,0,0.06)", "#1a1a1a"
        self._card.setStyleSheet(
            f"#loadingCard{{background:{card_bg};border:1px solid {border};"
            f"border-radius:16px;}}")
        self._label.setStyleSheet(f"color:{text};font-size:14px;font-weight:500;")

    def paintEvent(self, event):
        # Translucent backdrop that dims + visually "locks" the window.
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90) if isDarkTheme()
                   else QColor(245, 247, 250, 150))

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
        return super().eventFilter(obj, event)

    def show_with(self, text=None):
        self._apply_theme()
        self._label.setText(text or tr("Loading model", self._lang))
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()

    def retranslate(self, lang):
        self._lang = lang
