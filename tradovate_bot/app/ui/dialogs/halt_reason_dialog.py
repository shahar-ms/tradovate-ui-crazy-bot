"""Shown when the operator wants to clear a halt state."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
                               QWidget)


class HaltReasonDialog(QDialog):
    def __init__(self, reason: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Resume from halt?")
        self.setModal(True)
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        lbl = QLabel(f"<b>Halt reason:</b><br>{reason}")
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        self.ack = QCheckBox(
            "I investigated the cause and it is safe to resume. "
            "The bot will restart in PAPER mode."
        )
        root.addWidget(self.ack)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Resume")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.ack.toggled.connect(
            lambda checked: self.buttons.button(QDialogButtonBox.Ok).setEnabled(checked)
        )
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
