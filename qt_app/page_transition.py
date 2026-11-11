"""Page-switch animation, done on snapshots.

The stock animation moved the REAL page 76 px up over 250 ms: every frame
re-rasterized the whole page (2-20 ms, more on a wide window) on Qt's fixed
60 Hz animation clock, and the page's own reload work ran first on the UI
thread (100-230 ms for live voice / proofread / history), so a switch started
with a dozen dropped frames.

Here both pages are painted once into pixmaps and only those are animated, on
a timer that follows the screen's refresh rate; the page's reload work is run
by the caller when ``finished`` fires. The motion is the one the web apps use
for a sidebar switch: the old page fades out in 160 ms while it moves 12 px
the way the new one came from, the new page arrives from 12 px away on a
critically damped spring (430 ms). Only position and opacity change.
"""
from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QEasingCurve, QPointF, QPoint, Signal
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

OUT_MS, IN_MS, SHIFT = 160, 430, 12
# SwiftUI's .smooth spring (bounce 0), sampled at 33 points: 90% of the way in
# the first 60% of the time, so 430 ms reads faster than an eased 430 ms.
_SPRING = (0, 0.025, 0.084, 0.163, 0.25, 0.337, 0.422, 0.501, 0.572, 0.636, 0.692, 0.74, 0.782, 0.818, 0.849,
           0.874, 0.896, 0.914, 0.929, 0.942, 0.952, 0.961, 0.968, 0.974, 0.979, 0.983, 0.986, 0.989, 0.991,
           0.992, 0.994, 0.995, 1)
_EXIT = QEasingCurve(QEasingCurve.BezierSpline)
_EXIT.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(1.0, 1.0), QPointF(1.0, 1.0))


def spring(t):
    """Position of the spring at t in 0..1 (linear between the samples)."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    x = t * (len(_SPRING) - 1)
    i = int(x)
    return _SPRING[i] + (_SPRING[i + 1] - _SPRING[i]) * (x - i)


def snapshot(widget):
    """The widget as it looks now, on a transparent pixmap (QWidget.grab would
    paint the window's flat background behind it and hide the sky)."""
    dpr = widget.devicePixelRatioF()
    pm = QPixmap(max(1, round(widget.width() * dpr)), max(1, round(widget.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    widget.render(pm, QPoint(), QRegion(), QWidget.DrawChildren)
    return pm


class PageTransition(QWidget):
    """Covers the page area while one page is exchanged for another."""
    finished = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        # It paints every pixel (the sky's snapshot first), so nothing under it
        # is repainted while it runs; clicks go through to the real page.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._back = self._old = self._new = None
        self._dir = 1
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def running(self):
        return self._timer.isActive()

    def run(self, backdrop, old, new, direction):
        """backdrop: what is behind the pages (opaque); old / new: page snapshots;
        direction: +1 when the new page is further down the navigation."""
        self._back, self._old, self._new, self._dir = backdrop, old, new, 1 if direction >= 0 else -1
        screen = self.screen()
        hz = screen.refreshRate() if screen is not None else 60.0
        self._timer.setInterval(max(4, round(1000.0 / max(hz, 30.0))))
        self._clock.start()
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def stop(self):
        """End now (the next switch came, or the window is closing)."""
        if not self._timer.isActive():
            return
        self._timer.stop()
        self.hide()
        self._back = self._old = self._new = None
        self.finished.emit()

    def _tick(self):
        if self._clock.elapsed() >= IN_MS:
            self.stop()
        else:
            self.update()

    def paintEvent(self, _e):
        if self._back is None:
            return
        ms = self._clock.elapsed()
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._back)
        if ms < OUT_MS:
            k = _EXIT.valueForProgress(ms / OUT_MS)
            p.setOpacity(1.0 - k)
            p.drawPixmap(QPointF(0, -self._dir * SHIFT * k), self._old)
        k = spring(ms / IN_MS)
        p.setOpacity(k)
        p.drawPixmap(QPointF(0, self._dir * SHIFT * (1.0 - k)), self._new)
        p.end()
