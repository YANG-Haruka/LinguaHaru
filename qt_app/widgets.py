"""Shared visual building blocks for the Fluent-styled Qt UI.

Pure presentation widgets (no backend imports beyond i18n). Used by the
Translate page (colorful format-category cards), the Plugins / Interface pages
(provider entry cards) and the progress dashboard (metric cards).
"""

import os

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QElapsedTimer
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget

from qfluentwidgets import (
    CardWidget, SimpleCardWidget, IconWidget, CaptionLabel,
    StrongBodyLabel, TitleLabel, FluentIcon, isDarkTheme, InfoBadge, themeColor,
)

from core.paths import ASSETS_DIR

_FILETYPES_DIR = os.path.join(ASSETS_DIR, "icons", "filetypes")


class FormatCategoryCard(SimpleCardWidget):
    """A colorful rounded card for a file-format category (Books / Documents /
    Subtitles / ...). Shows a tinted icon chip, a title and the format list.

    Optional categories (PDF / Image / Media) can be marked unavailable when
    their plugin isn't installed: the card dims, shows an 'unavailable' badge,
    and emits ``clicked`` (inherited from SimpleCardWidget) so the page can
    prompt the user to install it."""

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


# File-type chips that drift across the drop zone background: (suffix, svg key
# under assets/icons/filetypes/). Each chip = the SVG document icon + the suffix.
_FILE_TYPES = [
    (".pdf", "pdf"), (".docx", "docx"), (".pptx", "pptx"), (".xlsx", "xlsx"),
    (".epub", "epub"), (".txt", "txt"), (".md", "md"), (".srt", "srt"),
    (".vtt", "srt"), (".csv", "csv"), (".json", "json"), (".html", "html"),
    (".png", "img"), (".jpg", "img"), (".mp4", "media"), (".mp3", "media"),
]


class DropZone(SimpleCardWidget):
    """A big clickable upload region with a dashed border that also accepts
    drag-and-dropped files (mirrors the Web UI's dropzone). A marquee of
    file-type chips (a coloured document icon + suffix, e.g. red ".pdf",
    blue ".docx") drifts across the background. ``clicked`` is inherited from
    SimpleCardWidget; ``filesDropped`` carries the dropped local paths."""

    filesDropped = Signal(list)
    _CHIP_GAP = 30

    def __init__(self, prompt, icon=FluentIcon.CLOUD, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self._hot = False           # highlighted while a drag hovers over it
        self._scroll = 0.0          # marquee offset in px

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)

        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(40, 40)
        layout.addWidget(self.icon, 0, Qt.AlignHCenter)

        self.prompt = StrongBodyLabel(prompt, self)
        self.prompt.setAlignment(Qt.AlignCenter)
        self.prompt.setWordWrap(True)
        layout.addWidget(self.prompt, 0, Qt.AlignHCenter)

        # ~60 fps marquee animation (paused while hidden). Motion is delta-time
        # based so the speed is the same regardless of frame rate and stays
        # smooth if a frame is late.
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_prompt(self, text):
        self.prompt.setText(text)

    def _tick(self):
        if not self._clock.isValid():
            self._clock.start()
            dt = 0.0166
        else:
            dt = self._clock.restart() / 1000.0
        if dt <= 0 or dt > 0.1:
            dt = 0.0166
        self._scroll += 18.0 * dt  # ≈ 18 px/s, frame-rate independent
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hot = True
            self.update()

    def dragLeaveEvent(self, _event):
        self._hot = False
        self.update()

    def dropEvent(self, event):
        self._hot = False
        self.update()
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)

    _ICON_H = 32
    _ICON_W = round(32 * 48 / 60)   # SVG viewBox is 48x60
    _svg_cache = {}

    def _icon_pixmap(self, key):
        """Render (and cache) a file-type SVG to a QPixmap at icon height."""
        dpr = self.devicePixelRatioF() or 1.0
        ck = (key, round(dpr, 2))
        pm = DropZone._svg_cache.get(ck)
        if pm is None:
            r = QSvgRenderer(os.path.join(_FILETYPES_DIR, key + ".svg"))
            pm = QPixmap(round(self._ICON_W * dpr), round(self._ICON_H * dpr))
            pm.fill(Qt.transparent)
            pm.setDevicePixelRatio(dpr)
            pp = QPainter(pm)
            pp.setRenderHint(QPainter.Antialiasing, True)
            r.render(pp)
            pp.end()
            DropZone._svg_cache[ck] = pm
        return pm

    def _chip_width(self, fm, suffix):
        return self._ICON_W + 5 + fm.horizontalAdvance(suffix)

    def _draw_chip(self, p, x, yc, suffix, key, opacity):
        """Draw one chip: the SVG document icon + its suffix (e.g. '.pdf')."""
        p.setOpacity(opacity)
        pm = self._icon_pixmap(key)
        p.drawPixmap(int(x), int(yc - self._ICON_H / 2), pm)
        col = QColor(70, 80, 95) if not isDarkTheme() else QColor(200, 210, 225)
        p.setPen(col)
        fm = p.fontMetrics()
        p.drawText(int(x + self._ICON_W + 5), int(yc + fm.ascent() / 2 - 1), suffix)
        p.setOpacity(1.0)

    def _draw_chip_row(self, p, widths, period, yc, phase, opacity):
        x = -((self._scroll + phase) % period) - period
        while x < self.width():
            cx = x
            for (suffix, key), w in zip(_FILE_TYPES, widths):
                if -60 < cx < self.width() + 60:
                    self._draw_chip(p, cx, yc, suffix, key, opacity)
                cx += w
            x += period

    def paintEvent(self, event):
        super().paintEvent(event)  # rounded card background
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        accent = themeColor()
        r = self.rect().adjusted(2, 2, -2, -2)

        # Drifting file-type icons behind the prompt (clipped to the card).
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 10, 10)
        p.save()
        p.setClipPath(path)
        f = QFont(self.font()); f.setPointSize(11); f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        widths = [self._chip_width(fm, s) + self._CHIP_GAP for s, _ in _FILE_TYPES]
        period = sum(widths) or 1
        opacity = 0.85 if self._hot else 0.5
        self._draw_chip_row(p, widths, period, self.height() * 0.27, 0, opacity)
        self._draw_chip_row(p, widths, period, self.height() * 0.77, period * 0.5, opacity)
        p.restore()

        if self._hot:  # subtle accent wash while dragging over
            fill = QColor(accent)
            fill.setAlpha(28)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 10, 10)
        pen_color = QColor(accent)
        pen_color.setAlpha(190 if self._hot else 110)
        pen = QPen(pen_color)
        pen.setStyle(Qt.DashLine)
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 10, 10)
        p.end()


class MetricCard(SimpleCardWidget):
    """A dashboard metric: muted label on top, big value, optional sub-text."""

    def __init__(self, label, value="-", sub="", icon=None, accent="#0d83d6", parent=None):
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
    optional active badge. ``clicked`` is inherited from CardWidget; we add
    ``doubleClicked`` (used to open the config dialog)."""

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

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit()
