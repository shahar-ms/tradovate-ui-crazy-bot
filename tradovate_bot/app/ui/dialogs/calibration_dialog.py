"""
Modal dialog wrapping the existing CalibrationPage widget.

Why not rewrite calibration? The user explicitly likes the current flow.
We just change the shell from "a page in a nav" to "a modal window the HUD
launches on demand." All the drag-region / click-point / save / validate /
delete behavior from CalibrationPage is reused verbatim.

The dialog can be maximized for pixel-precise work on large monitors:
  - title-bar maximize button (standard Windows chrome)
  - inline "Maximize / Restore" button next to Close
  - F11 toggles maximize / restore
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QPushButton,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.pages.calibration_page import CalibrationPage


class CalibrationDialog(QDialog):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Tradovate bot — Calibration")
        self.setModal(True)
        self.resize(1200, 800)

        # Allow the standard title-bar maximize + minimize buttons. Keep the
        # stays-on-top hint so the dialog doesn't fall behind the HUD (which
        # is also always-on-top).
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowStaysOnTopHint
        )

        self.page = CalibrationPage(signals, self)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self.page, 1)

        # bottom bar: Maximize/Restore toggle on the left, Close on the right
        btn_row = QHBoxLayout()
        self.maximize_btn = QPushButton("Maximize")
        self.maximize_btn.setToolTip(
            "Maximize the calibration window for pixel-precise marking.\n"
            "Shortcut: F11. Click again (or press F11) to restore."
        )
        self.maximize_btn.clicked.connect(self._toggle_maximize)
        btn_row.addWidget(self.maximize_btn)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

        # F11 toggles maximize
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_maximize)

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.maximize_btn.setText("Maximize")
        else:
            self.showMaximized()
            self.maximize_btn.setText("Restore")
