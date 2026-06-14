"""Shared visual building blocks for the Fluent-styled Qt UI.

Pure presentation widgets (no backend imports beyond i18n). Used by the
Translate page (colorful format-category cards), the Plugins / Interface pages
(provider entry cards) and the progress dashboard (metric cards).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget

from qfluentwidgets import (
    CardWidget, SimpleCardWidget, IconWidget, CaptionLabel,
    StrongBodyLabel, TitleLabel, FluentIcon, isDarkTheme, InfoBadge,
)


class FormatCategoryCard(SimpleCardWidget):
    """A colorful rounded card for a file-format category (Books / Documents /
    Subtitles / ...). Shows a tinted icon chip, a title and the format list.

    Optional categories (PDF / Image / Media) can be marked unavailable when
    their plugin isn't installed: the card dims, shows an 'unavailable' badge,
    and emits ``clicked`` so the page can prompt the user to install it."""

    clicked = Signal()

    def __init__(self, title, formats, color, icon=FluentIcon.DOCUMENT,
                 module_key=None, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.module_key = module_key      # None = always-available core format
        self._available = True
        self._unavail_text = ""
        self.setFixedHeight(96)
        self.setMinimumWidth(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._chip = QWidget(self)
        self._chip.setFixedSize(30, 30)
        chip_layout = QVBoxLayout(self._chip)
        chip_layout.setContentsMargins(6, 6, 6, 6)
        self._icon = IconWidget(icon, self._chip)
        chip_layout.addWidget(self._icon)
        top.addWidget(self._chip)
        self.title = StrongBodyLabel(title, self)
        top.addWidget(self.title)
        top.addStretch(1)
        self._badge = None
        self._badge_row = top
        layout.addLayout(top)

        self.formats = CaptionLabel(formats, self)
        self.formats.setWordWrap(True)
        layout.addWidget(self.formats)
        layout.addStretch(1)

        self._apply_color()

    def set_title(self, text):
        self.title.setText(text)

    def set_available(self, available, unavailable_text=""):
        """Mark this category available or not (only meaningful for optional
        categories). ``unavailable_text`` is shown as a small badge."""
        self._available = available
        self._unavail_text = unavailable_text
        if self._badge is not None:
            self._badge.deleteLater()
            self._badge = None
        if not available and unavailable_text:
            self._badge = InfoBadge.warning(unavailable_text, self)
            self._badge_row.addWidget(self._badge)
        self.setCursor(Qt.PointingHandCursor if self.module_key else Qt.ArrowCursor)
        self._apply_color()

    def _apply_color(self):
        c = self._color
        dim = (not self._available)
        chip_alpha = 90 if dim else 255
        self._chip.setStyleSheet(
            f"background-color: rgba({c.red()},{c.green()},{c.blue()},{chip_alpha});"
            "border-radius: 8px;")
        self._icon.setStyleSheet("background: transparent;")
        # Left accent + faint card tint (greyed out when unavailable).
        bg_alpha = (18 if dim else (40 if not isDarkTheme() else 55))
        border_alpha = 90 if dim else 255
        self.setStyleSheet(
            f"FormatCategoryCard {{ border-left: 4px solid "
            f"rgba({c.red()},{c.green()},{c.blue()},{border_alpha});"
            f" background-color: rgba({c.red()},{c.green()},{c.blue()},{bg_alpha}); }}")

    def refresh_theme(self):
        self._apply_color()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()


class MetricCard(SimpleCardWidget):
    """A dashboard metric: muted label on top, big value, optional sub-text."""

    def __init__(self, label, value="-", sub="", icon=None, accent="#0078d4", parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 110)
        self._accent = QColor(accent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        if icon is not None:
            chip = QWidget(self)
            chip.setFixedSize(24, 24)
            cl = QVBoxLayout(chip)
            cl.setContentsMargins(4, 4, 4, 4)
            iw = IconWidget(icon, chip)
            cl.addWidget(iw)
            c = self._accent
            chip.setStyleSheet(
                f"background-color: rgba({c.red()},{c.green()},{c.blue()},45);"
                "border-radius: 6px;")
            head.addWidget(chip)
        self.label = CaptionLabel(label, self)
        head.addWidget(self.label)
        head.addStretch(1)
        layout.addLayout(head)

        self.value = TitleLabel(value, self)
        self.value.setStyleSheet("font-family: 'Consolas','DejaVu Sans Mono',monospace;")
        layout.addWidget(self.value)

        self.sub = CaptionLabel(sub, self)
        self.sub.setTextColor(QColor(130, 130, 130), QColor(160, 160, 160))
        layout.addWidget(self.sub)
        layout.addStretch(1)

    def set_value(self, value, sub=None):
        self.value.setText(str(value))
        if sub is not None:
            self.sub.setText(str(sub))

    def set_label(self, text):
        self.label.setText(text)


class RingMetricCard(SimpleCardWidget):
    """A metric card built around a ProgressRing (task progress)."""

    def __init__(self, label, parent=None):
        from qfluentwidgets import ProgressRing
        super().__init__(parent)
        self.setMinimumSize(180, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        self.label = CaptionLabel(label, self)
        layout.addWidget(self.label, 0, Qt.AlignLeft)

        self.ring = ProgressRing(self)
        self.ring.setFixedSize(120, 120)
        self.ring.setTextVisible(True)
        self.ring.setValue(0)
        layout.addWidget(self.ring, 0, Qt.AlignCenter)
        layout.addStretch(1)

    def set_value(self, percent):
        self.ring.setValue(int(percent))

    def set_label(self, text):
        self.label.setText(text)


class EntryCard(CardWidget):
    """A clickable provider/interface entry. Shows an icon, a name and an
    optional active badge. Emits ``clicked`` when pressed and ``doubleClicked``
    on double-click (used to open the config dialog)."""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, name, subtitle="", icon=FluentIcon.ROBOT, active=False, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setMinimumWidth(220)
        self._name = name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(28, 28)
        layout.addWidget(self.icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.name_label = StrongBodyLabel(name, self)
        text_col.addWidget(self.name_label)
        self.sub_label = CaptionLabel(subtitle, self)
        self.sub_label.setTextColor(QColor(130, 130, 130), QColor(160, 160, 160))
        text_col.addWidget(self.sub_label)
        layout.addLayout(text_col, 1)

        self.badge = None
        self.set_active(active)

    def set_active(self, active):
        from qfluentwidgets import InfoBadge
        if self.badge is not None:
            self.badge.deleteLater()
            self.badge = None
        if active:
            self.badge = InfoBadge.success("✓", self)
            self.layout().addWidget(self.badge)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
