"""
Modal dialog wrapping the existing CalibrationPage widget.

Why not rewrite calibration? The user explicitly likes the current flow.
We just change the shell from "a page in a nav" to "a modal window the HUD
launches on demand." All the drag-region / click-point / save / validate /
delete behavior from CalibrationPage is reused verbatim.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from app.ui.app_signals import AppSignals
from app.ui.pages.calibration_page import CalibrationPage


class CalibrationDialog(QDialog):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Tradovate bot — Calibration")
        self.setModal(True)
        self.resize(1200, 800)
        # keep on top of the HUD so the operator isn't hunting for this window
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.page = CalibrationPage(signals, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self.page, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
