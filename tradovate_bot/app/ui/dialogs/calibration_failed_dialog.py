"""
Shown when bootstrap rejects a Start because live calibration validation
failed (typically: anchor similarity below threshold).

Three exits:
  - Re-calibrate   -> nav to the Calibration page
  - Start anyway   -> retry start with skip_calibration_check=True
  - Cancel         -> do nothing
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from app.ui.theme import ARM_ORANGE, BROKEN_RED, TEXT_MUTED


class CalibrationFailedDialog(QDialog):
    RECALIBRATE = 1
    START_ANYWAY = 2
    CANCEL = 3

    def __init__(self, message: str, report_lines: list[str],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Calibration check failed")
        self.setModal(True)
        self.setMinimumSize(640, 420)
        self._choice = self.CANCEL

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # headline
        head = QLabel(f"<b>Can't start the bot right now.</b>")
        head.setStyleSheet(f"font-size: 14px; color: {BROKEN_RED};")
        root.addWidget(head)

        # short summary
        summary = QLabel(
            f"Bootstrap ran the live calibration validator and it failed:\n\n{message}"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        # full report
        report_label = QLabel("Full validator output:")
        report_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(report_label)

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText("\n".join(report_lines) if report_lines else "(no detail)")
        box.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        root.addWidget(box, 1)

        hint = QLabel(
            "<b>Most common cause:</b> the live anchor region no longer matches the "
            "saved reference — because the Tradovate window moved, was resized, or "
            "is covered by another window. Move Tradovate back to where it was when "
            "you calibrated, or re-calibrate."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(hint)

        # buttons
        row = QHBoxLayout()
        row.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        row.addWidget(cancel_btn)

        skip_btn = QPushButton("Start anyway (skip anchor check)")
        skip_btn.setToolTip(
            "Boot the bot without the live anchor check. The runtime watchdog "
            "will still catch anchor drift once the bot is running."
        )
        skip_btn.setProperty("role", "arm")
        skip_btn.clicked.connect(self._start_anyway)
        row.addWidget(skip_btn)

        recal_btn = QPushButton("Re-calibrate")
        recal_btn.setDefault(True)
        recal_btn.setProperty("role", "primary")
        recal_btn.clicked.connect(self._recalibrate)
        row.addWidget(recal_btn)

        root.addLayout(row)

    # --- actions --- #

    def _cancel(self) -> None:
        self._choice = self.CANCEL
        self.reject()

    def _start_anyway(self) -> None:
        self._choice = self.START_ANYWAY
        self.accept()

    def _recalibrate(self) -> None:
        self._choice = self.RECALIBRATE
        self.accept()

    @property
    def choice(self) -> int:
        return self._choice
