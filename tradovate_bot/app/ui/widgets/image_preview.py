"""
Image preview widget that scales PNGs proportionally.
Loads from a filesystem path and re-renders on resize.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


class ImagePreview(QLabel):
    def __init__(self, placeholder: str = "No image", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignCenter)
        self.setText(self._placeholder)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(180)
        self.setStyleSheet("border: 1px dashed #2f3947; border-radius: 4px; color: #8d97a5;")

    def load_path(self, path: Path | str) -> bool:
        p = Path(path)
        if not p.exists():
            self.clear_image()
            return False
        pix = QPixmap(str(p))
        if pix.isNull():
            self.clear_image()
            return False
        self._pixmap = pix
        self._render()
        return True

    def clear_image(self) -> None:
        self._pixmap = None
        self.setText(self._placeholder)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._pixmap is None:
            return
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(scaled)
