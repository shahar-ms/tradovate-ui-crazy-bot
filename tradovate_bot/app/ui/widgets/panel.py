"""Titled panel used across dashboard / settings pages."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Panel(QFrame):
    def __init__(self, title: str, alt: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panelAlt" if alt else "panel")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 10, 12, 12)
        self._lay.setSpacing(8)

        self._title = QLabel(title)
        self._title.setProperty("role", "title")
        self._lay.addWidget(self._title)

    def add(self, widget: QWidget) -> None:
        self._lay.addWidget(widget)

    def add_stretch(self) -> None:
        self._lay.addStretch(1)
