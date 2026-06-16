"""Animated sky background for the Qt app — the desktop twin of the Web UI's
``background.js`` canvas.

Day (light theme): a Makoto-Shinkai sky — luminous gradient, a warm sun bloom
and drifting flat-bottomed cumulus, with a few floating motes.
Night (dark theme): deep-navy void with slow nebula glow, multi-depth twinkling
stars, faint moonlit clouds and the occasional shooting star.

It is a full-window widget kept at the bottom of the z-order behind the
(transparent) content area, so the sky shows through everywhere the UI's cards
don't cover. Painting is driven by a ~30 fps QTimer that pauses when hidden.
"""

import math
import random

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QElapsedTimer
from PySide6.QtGui import (
    QPainter, QColor, QPixmap, QImage, QRadialGradient, QLinearGradient, QPen,
)
from PySide6.QtWidgets import QWidget

TAU = math.tau


def _rand(a, b):
    return a + random.random() * (b - a)


class SkyBackground(QWidget):
    def __init__(self, parent=None, mode="day"):
        super().__init__(parent)
        self._mode = mode
        # Sit behind everything and never steal clicks.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)  # we fill every pixel
        self.lower()

        self._t = 0.0
        self._next_meteor = 2.0
        self._stars = []
        self._nebula = []
        self._meteors = []
        self._clouds = []      # each: dict with day/night sprite + position
        self._motes = []
        self._backdrop = None  # cached static layer (gradient + sun/nebula)

        # Delta-time animation: motion is scaled by real elapsed time, so it runs
        # at the same speed regardless of frame rate and stays smooth if a frame
        # is late. _fs is the per-frame scale relative to the original 30fps tuning.
        self._clock = QElapsedTimer()
        self._fs = 1.0
        self._dt = 0.0166

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60 fps (frames are cheap: blit + sprites)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ #
    def set_mode(self, mode):
        """mode: 'day' (light theme) or 'night' (dark theme)."""
        new = "day" if mode in ("day", "light") else "night"
        if new != self._mode:
            self._mode = new
            self._meteors = []
            self._build_backdrop()
            self.update()

    # ------------------------------------------------------------------ #
    def _tick(self):
        # Real elapsed seconds since the last frame (clamped so a long stall
        # doesn't make everything jump). _fs scales per-frame motion to the
        # original 30fps baseline.
        if not self._clock.isValid():
            self._clock.start()
            dt = 0.0166
        else:
            dt = self._clock.restart() / 1000.0
        if dt <= 0 or dt > 0.1:
            dt = 0.0166
        self._dt = dt
        self._fs = dt * 30.0
        self._t += dt
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    # Pause/resume the animation around page transitions: freezing this
    # full-window repaint while a page slides in frees the whole frame budget
    # for the slide animation, so switching pages stays smooth (the ~300ms
    # freeze of cloud drift is imperceptible).
    def pause(self):
        self._timer.stop()

    def resume(self):
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._build_scene()
        self._build_backdrop()

    # ------------------------------------------------------------------ #
    # Scene construction (depends on widget size; rebuilt on resize)
    # ------------------------------------------------------------------ #
    def _build_scene(self):
        w, h = max(1, self.width()), max(1, self.height())
        self._init_stars(w, h)
        self._init_nebula()
        self._init_clouds(w, h)
        self._init_motes(w, h)

    def _init_stars(self, w, h):
        self._stars = []
        n = min(360, round((w * h) / 7000))
        for _ in range(n):
            depth = random.random()
            self._stars.append({
                "x": random.random() * w, "y": random.random() * h,
                "r": 0.4 + depth * 1.5, "a": 0.25 + random.random() * 0.6,
                "tw": 0.4 + random.random() * 1.7, "ph": random.random() * TAU,
            })

    def _init_nebula(self):
        self._nebula = [
            {"x": 0.20, "y": 0.16, "r": 0.55, "c": (63, 120, 255), "a": 0.10},
            {"x": 0.86, "y": 0.10, "r": 0.42, "c": (46, 92, 200), "a": 0.08},
            {"x": 0.60, "y": 0.52, "r": 0.62, "c": (96, 72, 210), "a": 0.06},
        ]

    def _init_clouds(self, w, h):
        self._clouds = []
        n = max(4, round(w / 360))
        for i in range(n):
            scale = _rand(0.7, 1.5)
            day = self._make_cloud_sprite(scale, night=False)
            night = self._make_cloud_sprite(scale, night=True)
            self._clouds.append({
                "x": (i / n) * (w + 300) - 150,
                "y": h * _rand(0.10, 0.60),
                "spr_day": day, "spr_night": night,
                "w": day.width(), "h": day.height(),
                # Clearly visible drift (the old 0.05-0.14 read as static).
                "vx": _rand(0.35, 0.75), "bob": random.random() * TAU,
            })

    def _init_motes(self, w, h):
        self._motes = []
        n = min(90, round((w * h) / 30000))
        for _ in range(n):
            self._motes.append({
                "x": random.random() * w, "y": random.random() * h,
                "r": 0.6 + random.random() * 1.8, "a": 0.1 + random.random() * 0.32,
                "vy": -_rand(0.1, 0.32), "vx": _rand(0.04, 0.13),
                "ph": random.random() * TAU,
            })

    def _make_cloud_sprite(self, scale, night):
        """Pre-render one soft, flat-bottomed cumulus to a QPixmap (additive
        puffs + a shaded underside), mirroring background.js makeCloud()."""
        w, h = int(360 * scale), int(190 * scale)
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setCompositionMode(QPainter.CompositionMode_Plus)  # accumulate softly
        base_y = h * 0.70
        puffs = random.randint(10, 13)
        for i in range(puffs):
            f = 0.5 if puffs == 1 else i / (puffs - 1)
            mid = 1 - abs(f - 0.5) * 2
            pr = h * (0.14 + 0.26 * mid) * _rand(0.85, 1.18)
            px = w * (0.14 + 0.72 * f) + _rand(-w * 0.02, w * 0.02)
            py = base_y - pr * _rand(0.30, 0.80)
            g = QRadialGradient(px, py, pr)
            if night:  # faint moonlit blue-gray mass, low alpha so stars survive
                g.setColorAt(0.0, QColor(120, 140, 180, 64))
                g.setColorAt(0.5, QColor(95, 115, 155, 34))
                g.setColorAt(1.0, QColor(95, 115, 155, 0))
            else:      # pure white cumulus
                g.setColorAt(0.0, QColor(255, 255, 255, 87))
                g.setColorAt(0.5, QColor(255, 255, 255, 46))
                g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(px, py), pr, pr)
        # Cool shading hugging the underside -> grounds the cloud.
        p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        shade = QLinearGradient(0, base_y - h * 0.18, 0, base_y + h * 0.08)
        if night:
            shade.setColorAt(0.0, QColor(8, 12, 24, 0))
            shade.setColorAt(1.0, QColor(8, 12, 24, 110))
        else:
            shade.setColorAt(0.0, QColor(176, 202, 228, 0))
            shade.setColorAt(1.0, QColor(150, 180, 214, 77))
        p.fillRect(QRectF(0, 0, w, h), shade)
        p.end()
        return QPixmap.fromImage(img)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def _build_backdrop(self):
        """Pre-render the static layer (sky gradient + sun bloom for day, or
        gradient + nebula for night) to a QPixmap. It's identical every frame,
        so caching it turns each paint into a blit + a few cheap moving sprites
        instead of redrawing full-window gradients 60×/second."""
        w, h = max(1, self.width()), max(1, self.height())
        if not self._nebula:
            self._init_nebula()
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(round(w * dpr), round(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, w, h)
        if self._mode == "day":
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.00, QColor("#3a93cf"))
            g.setColorAt(0.42, QColor("#78bfe6"))
            g.setColorAt(0.72, QColor("#c2e4f1"))
            g.setColorAt(1.00, QColor("#fbe6cd"))
            p.fillRect(rect, g)
            sx, sy = w * 0.82, h * 0.18
            sg = QRadialGradient(sx, sy, max(w, h) * 0.55)
            sg.setColorAt(0.00, QColor(255, 249, 235, 178))
            sg.setColorAt(0.07, QColor(255, 243, 214, 102))
            sg.setColorAt(0.28, QColor(255, 228, 186, 31))
            sg.setColorAt(1.00, QColor(255, 226, 182, 0))
            p.setCompositionMode(QPainter.CompositionMode_Screen)
            p.fillRect(rect, sg)
        else:
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, QColor("#05080f"))
            g.setColorAt(0.6, QColor("#0a1222"))
            g.setColorAt(1.0, QColor("#0d1830"))
            p.fillRect(rect, g)
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            for nb in self._nebula:
                cx, cy = nb["x"] * w, nb["y"] * h
                ng = QRadialGradient(cx, cy, nb["r"] * max(w, h))
                r, gg, b = nb["c"]
                ng.setColorAt(0.0, QColor(r, gg, b, int(nb["a"] * 255)))
                ng.setColorAt(1.0, QColor(r, gg, b, 0))
                p.fillRect(rect, ng)
        p.end()
        self._backdrop = pm

    def paintEvent(self, _e):
        if not self._clouds and self.width() > 1:
            self._build_scene()
        if self._backdrop is None and self.width() > 1:
            self._build_backdrop()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._backdrop is not None:
            p.drawPixmap(0, 0, self._backdrop)
        if self._mode == "day":
            self._draw_day(p)
        else:
            self._draw_night(p)
        p.end()

    # ---------- day ----------
    def _draw_day(self, p):
        # Sky gradient + sun bloom are in the cached backdrop; only the moving
        # layers are drawn here, with motion scaled by delta-time (_fs).
        w, h = self.width(), self.height()
        fs = self._fs

        # Drifting clouds.
        for c in self._clouds:
            c["x"] += c["vx"] * fs
            if c["x"] - c["w"] > w:
                c["x"] = -c["w"] - _rand(0, 200)
            by = c["y"] + math.sin(self._t * 0.3 + c["bob"]) * 4
            p.setOpacity(0.94)
            p.drawPixmap(int(c["x"]), int(by), c["spr_day"])
        p.setOpacity(1.0)

        # Floating motes.
        p.setPen(Qt.NoPen)
        for m in self._motes:
            m["y"] += m["vy"] * fs
            m["x"] += m["vx"] * fs
            if m["y"] < -6:
                m["y"] = h + 6
                m["x"] = random.random() * w
            if m["x"] > w + 6:
                m["x"] = -6
            a = m["a"] * (0.55 + 0.45 * math.sin(self._t * 1.5 + m["ph"]))
            p.setBrush(QColor(255, 248, 234, max(0, min(255, int(a * 255)))))
            p.drawEllipse(QPointF(m["x"], m["y"]), m["r"], m["r"])

    # ---------- night ----------
    def _draw_night(self, p):
        # Gradient + nebula are in the cached backdrop; only the moving layers
        # are drawn here, with motion scaled by delta-time (_fs).
        w, h = self.width(), self.height()
        fs = self._fs

        # Faint moonlit clouds drifting low and slow (kept dim so stars read).
        for c in self._clouds:
            c["x"] += c["vx"] * 0.6 * fs
            if c["x"] - c["w"] > w:
                c["x"] = -c["w"] - _rand(0, 200)
            by = c["y"] + math.sin(self._t * 0.25 + c["bob"]) * 3
            p.setOpacity(0.55)
            p.drawPixmap(int(c["x"]), int(by), c["spr_night"])
        p.setOpacity(1.0)

        # Twinkling stars.
        p.setPen(Qt.NoPen)
        for s in self._stars:
            tw = 0.5 + 0.5 * math.sin(self._t * s["tw"] + s["ph"])
            a = s["a"] * tw
            p.setBrush(QColor(234, 242, 255, max(0, min(255, int(a * 255)))))
            p.drawEllipse(QPointF(s["x"], s["y"]), s["r"], s["r"])
            if s["r"] > 1.25:  # soft halo on the brightest
                p.setBrush(QColor(234, 242, 255, max(0, min(255, int(a * 0.22 * 255)))))
                p.drawEllipse(QPointF(s["x"], s["y"]), s["r"] * 3.4, s["r"] * 3.4)

        # Shooting stars.
        self._draw_meteors(p, w, h)

    def _draw_meteors(self, p, w, h):
        fs = self._fs
        for m in self._meteors:
            m["x"] += m["vx"] * fs
            m["y"] += m["vy"] * fs
            m["life"] += fs
            inv = 1.0 / math.hypot(m["vx"], m["vy"])
            tx = m["x"] - m["vx"] * inv * m["len"]
            ty = m["y"] - m["vy"] * inv * m["len"]
            fade = min(1.0, m["life"] / 7.0) * max(0.0, 1 - m["life"] / m["max"])
            lg = QLinearGradient(m["x"], m["y"], tx, ty)
            lg.setColorAt(0.0, QColor(255, 255, 255, int(0.9 * fade * 255)))
            lg.setColorAt(0.35, QColor(175, 205, 255, int(0.45 * fade * 255)))
            lg.setColorAt(1.0, QColor(130, 165, 255, 0))
            pen = QPen()
            pen.setBrush(lg)
            pen.setWidthF(1.7)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(m["x"], m["y"]), QPointF(tx, ty))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(fade * 255)))
            p.drawEllipse(QPointF(m["x"], m["y"]), 1.7, 1.7)

        self._meteors = [m for m in self._meteors
                         if m["life"] < m["max"] and m["y"] < h + 60
                         and -300 < m["x"] < w + 300]
        self._next_meteor -= self._dt
        if self._next_meteor <= 0:
            self._spawn_meteor(w, h)
            self._next_meteor = _rand(2.6, 5.5)

    def _spawn_meteor(self, w, h):
        from_left = random.random() < 0.5
        self._meteors.append({
            "x": _rand(w * 0.1, w * 0.9), "y": _rand(-60, h * 0.18),
            "vx": (1 if from_left else -0.7) * _rand(5, 9), "vy": _rand(7, 11),
            "len": _rand(140, 280), "life": 0, "max": _rand(48, 78),
        })
