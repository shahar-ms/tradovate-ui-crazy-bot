"""
ClickFlashOverlay — transient on-screen marker showing where the bot just
clicked. Frameless, translucent, always-on-top, and click-through (does not
steal focus or intercept mouse events), so it can safely be layered over
Tradovate while debugging.

Usage:
    overlay = ClickFlashOverlay()
    signals.click_dispatched.connect(overlay.flash)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


SIZE = 64           # total marker size in px
HOLD_MS = 700       # how long the marker stays visible


class ClickFlashOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput   # click-through
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(SIZE, SIZE)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def flash(self, x: int, y: int) -> None:
        """Show a crosshair/ring marker centered on (x, y) for HOLD_MS."""
        self.move(x - SIZE // 2, y - SIZE // 2)
        self.show()
        self.raise_()
        self._hide_timer.start(HOLD_MS)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        cx = cy = SIZE // 2

        # semi-opaque red fill ring
        p.setBrush(QBrush(QColor(255, 64, 64, 80)))
        p.setPen(QPen(QColor(255, 64, 64, 230), 3))
        p.drawEllipse(4, 4, SIZE - 8, SIZE - 8)

        # crosshair
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawLine(cx, 6, cx, SIZE - 6)
        p.drawLine(6, cy, SIZE - 6, cy)

        # center dot
        p.setBrush(QBrush(QColor(255, 255, 255, 255)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(cx - 3, cy - 3, 6, 6)
