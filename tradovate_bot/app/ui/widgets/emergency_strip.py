"""
Always-visible bottom emergency strip: Disarm, Cancel All, Halt.
These must work from any page and remain accessible.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


class EmergencyStrip(QFrame):
    disarm_clicked = Signal()
    cancel_all_clicked = Signal()
    halt_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panelAlt")
        self.setFixedHeight(64)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        self._meta = QLabel("—")
        self._meta.setProperty("role", "muted")
        lay.addWidget(self._meta)
        lay.addStretch(1)

        self._disarm = QPushButton("Disarm")
        self._disarm.setMinimumWidth(120)
        self._disarm.clicked.connect(self.disarm_clicked)

        self._cancel = QPushButton("Cancel All")
        self._cancel.setMinimumWidth(140)
        self._cancel.setProperty("role", "cancel")
        self._cancel.clicked.connect(self.cancel_all_clicked)

        self._halt = QPushButton("HALT")
        self._halt.setMinimumWidth(160)
        self._halt.setMinimumHeight(44)
        self._halt.setProperty("role", "halt")
        self._halt.setShortcut("Ctrl+H")
        self._halt.clicked.connect(self.halt_clicked)

        lay.addWidget(self._disarm)
        lay.addWidget(self._cancel)
        lay.addWidget(self._halt)

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)
