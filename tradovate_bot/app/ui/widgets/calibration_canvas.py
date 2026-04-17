"""
Canvas that shows a scaled screenshot and lets the user mark either a
rectangle (drag) or a point (click). Emits full-resolution coords
regardless of the widget size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget


def _bgr_to_qimage(img: np.ndarray) -> QImage:
    h, w = img.shape[:2]
    if img.ndim == 3 and img.shape[2] == 3:
        rgb = img[..., ::-1].copy()  # BGR -> RGB
        return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    if img.ndim == 2:
        return QImage(img.data, w, h, w, QImage.Format_Grayscale8).copy()
    raise ValueError(f"unsupported shape: {img.shape}")


@dataclass
class CanvasOverlay:
    kind: str                 # "region" | "point"
    label: str
    color: QColor
    # region fields (full-res image coords)
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    # point fields
    x: int = 0
    y: int = 0


class CalibrationCanvas(QWidget):
    region_marked = Signal(int, int, int, int)  # left, top, width, height in image coords
    point_marked = Signal(int, int)             # x, y in image coords

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(420)
        self.setStyleSheet("background-color: #0b0d12; border: 1px solid #2f3947;")
        self.setMouseTracking(True)

        self._image: Optional[QImage] = None
        self._image_size: tuple[int, int] = (0, 0)  # (w, h)
        self._mode: str = "idle"                    # idle | region | point
        self._drag_start_widget: Optional[QPoint] = None
        self._drag_end_widget: Optional[QPoint] = None
        self._point_widget: Optional[QPoint] = None
        self._overlays: list[CanvasOverlay] = []

    # ---- public API ---- #

    def set_image(self, img_bgr: np.ndarray) -> None:
        self._image = _bgr_to_qimage(img_bgr)
        self._image_size = (img_bgr.shape[1], img_bgr.shape[0])
        self._reset_drawing()
        self.update()

    def clear_image(self) -> None:
        self._image = None
        self._image_size = (0, 0)
        self._reset_drawing()
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def set_overlays(self, overlays: list[CanvasOverlay]) -> None:
        self._overlays = overlays
        self.update()

    def start_mark_region(self) -> None:
        self._mode = "region"
        self._reset_drawing()
        self.update()

    def start_mark_point(self) -> None:
        self._mode = "point"
        self._reset_drawing()
        self.update()

    def cancel_mark(self) -> None:
        self._mode = "idle"
        self._reset_drawing()
        self.update()

    # ---- coord helpers ---- #

    def _scale_and_offset(self) -> tuple[float, int, int, int, int]:
        """Return (scale, off_x, off_y, draw_w, draw_h)."""
        if self._image is None:
            return 1.0, 0, 0, 0, 0
        iw, ih = self._image_size
        ww, wh = self.width(), self.height()
        if iw <= 0 or ih <= 0 or ww <= 0 or wh <= 0:
            return 1.0, 0, 0, 0, 0
        scale = min(ww / iw, wh / ih)
        dw = int(iw * scale)
        dh = int(ih * scale)
        ox = (ww - dw) // 2
        oy = (wh - dh) // 2
        return scale, ox, oy, dw, dh

    def _widget_to_image(self, p: QPoint) -> Optional[tuple[int, int]]:
        scale, ox, oy, dw, dh = self._scale_and_offset()
        if scale == 0:
            return None
        x = p.x() - ox
        y = p.y() - oy
        if x < 0 or y < 0 or x > dw or y > dh:
            x = max(0, min(x, dw))
            y = max(0, min(y, dh))
        ix = int(round(x / scale))
        iy = int(round(y / scale))
        iw, ih = self._image_size
        ix = max(0, min(ix, iw - 1))
        iy = max(0, min(iy, ih - 1))
        return ix, iy

    # ---- mouse events ---- #

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._image is None or e.button() != Qt.LeftButton:
            return
        if self._mode == "region":
            self._drag_start_widget = e.position().toPoint()
            self._drag_end_widget = self._drag_start_widget
        elif self._mode == "point":
            self._point_widget = e.position().toPoint()
            self.update()
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._image is None:
            return
        if self._mode == "region" and self._drag_start_widget is not None \
                and (e.buttons() & Qt.LeftButton):
            self._drag_end_widget = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._image is None:
            return
        if self._mode == "region" and self._drag_start_widget is not None:
            self._drag_end_widget = e.position().toPoint()
            self.update()

    # ---- key events: commit / cancel ---- #

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.commit()
        elif e.key() == Qt.Key_Escape:
            self.cancel_mark()
        else:
            super().keyPressEvent(e)

    # ---- commit / cancel ---- #

    def commit(self) -> bool:
        """Emit the current drawing at full image resolution, return True if committed."""
        if self._mode == "region" and self._drag_start_widget and self._drag_end_widget:
            p1 = self._widget_to_image(self._drag_start_widget)
            p2 = self._widget_to_image(self._drag_end_widget)
            if p1 is None or p2 is None:
                return False
            left = min(p1[0], p2[0])
            top = min(p1[1], p2[1])
            right = max(p1[0], p2[0])
            bottom = max(p1[1], p2[1])
            w = max(1, right - left)
            h = max(1, bottom - top)
            self._mode = "idle"
            self._reset_drawing()
            self.update()
            self.region_marked.emit(left, top, w, h)
            return True
        if self._mode == "point" and self._point_widget:
            img = self._widget_to_image(self._point_widget)
            if img is None:
                return False
            self._mode = "idle"
            self._reset_drawing()
            self.update()
            self.point_marked.emit(img[0], img[1])
            return True
        return False

    # ---- rendering ---- #

    def _reset_drawing(self) -> None:
        self._drag_start_widget = None
        self._drag_end_widget = None
        self._point_widget = None

    def paintEvent(self, _):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b0d12"))

        if self._image is None:
            painter.setPen(QColor("#8d97a5"))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "No screenshot captured. Click 'Capture monitor' above.")
            return

        scale, ox, oy, dw, dh = self._scale_and_offset()
        painter.drawImage(QRect(ox, oy, dw, dh), self._image)

        # existing overlays
        for ov in self._overlays:
            pen = QPen(ov.color, 2)
            painter.setPen(pen)
            if ov.kind == "region":
                painter.drawRect(
                    int(ox + ov.left * scale),
                    int(oy + ov.top * scale),
                    int(ov.width * scale),
                    int(ov.height * scale),
                )
                painter.drawText(int(ox + ov.left * scale),
                                 int(oy + ov.top * scale) - 4,
                                 ov.label)
            elif ov.kind == "point":
                cx = int(ox + ov.x * scale)
                cy = int(oy + ov.y * scale)
                painter.drawEllipse(QPoint(cx, cy), 10, 10)
                painter.drawPoint(cx, cy)
                painter.drawText(cx + 12, cy - 6, ov.label)

        # in-progress drawing
        if self._mode == "region" and self._drag_start_widget and self._drag_end_widget:
            rect = QRect(self._drag_start_widget, self._drag_end_widget).normalized()
            painter.setPen(QPen(QColor("#35c46a"), 2, Qt.DashLine))
            painter.drawRect(rect)
        elif self._mode == "point" and self._point_widget:
            painter.setPen(QPen(QColor("#e04242"), 2))
            painter.drawEllipse(self._point_widget, 10, 10)
            painter.drawPoint(self._point_widget)
