"""
Two-step arm confirmation dialog.
  1. shows the full pre-arm check list + risk summary
  2. requires the operator to tick an acknowledgement checkbox and type ARM
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from app.ui.controller import UiController
from app.ui.theme import ARM_ORANGE, BROKEN_RED, OK_GREEN
from app.ui.ui_state import UiState


class ArmConfirmDialog(QDialog):
    CONFIRM_TEXT = "ARM"

    def __init__(self, controller: UiController, state: UiState,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.controller = controller
        self.state = state
        self.setWindowTitle("Arm live trading?")
        self.setModal(True)
        self.setMinimumWidth(540)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        warn = QLabel(
            "⚠  LIVE CLICKS will be sent to the Tradovate screen.\n"
            "   Make sure you are supervising this session, size is small, "
            "and the SIM account is selected if this is a trial."
        )
        warn.setStyleSheet(
            f"color: {ARM_ORANGE}; font-weight: 600; padding: 6px; "
            "border: 1px solid #55341a; border-radius: 4px;"
        )
        warn.setWordWrap(True)
        root.addWidget(warn)

        root.addWidget(QLabel("<b>Pre-arm checks</b>"))
        checks_box = QVBoxLayout()
        checks_box.setSpacing(4)
        all_pass = True
        for c in self.controller.pre_arm_checks():
            color = OK_GREEN if c.ok else BROKEN_RED
            icon = "✓" if c.ok else "✗"
            lbl = QLabel(f"<span style='color:{color}; font-weight:700;'>{icon}</span> "
                         f"{c.name} — {c.reason if not c.ok else 'ok'}")
            lbl.setTextFormat(Qt.RichText)
            checks_box.addWidget(lbl)
            all_pass &= c.ok
        root.addLayout(checks_box)

        # summary
        form = QFormLayout()
        form.setContentsMargins(0, 6, 0, 6)
        form.addRow("Mode after arm:", QLabel("ARMED"))
        form.addRow("Price health:", QLabel(state.price_stream_health))
        form.addRow("Anchor guard:", QLabel("ok" if state.anchor_ok else "drift"))
        form.addRow("Monitor:", QLabel(str(state.monitor_index)))
        form.addRow("Calibration loaded:", QLabel("yes" if state.calibration_loaded else "no"))
        root.addLayout(form)

        self.ack = QCheckBox(
            "I understand this will send real clicks. I am supervising the screen."
        )
        root.addWidget(self.ack)

        self.confirm_line = QLineEdit()
        self.confirm_line.setPlaceholderText(f"Type {self.CONFIRM_TEXT} to arm")
        root.addWidget(self.confirm_line)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Arm")
        self.buttons.button(QDialogButtonBox.Ok).setProperty("role", "arm")
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.ack.toggled.connect(self._update_ok)
        self.confirm_line.textChanged.connect(self._update_ok)
        self._all_pass = all_pass
        self._update_ok()

    def _update_ok(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(
            self._all_pass
            and self.ack.isChecked()
            and self.confirm_line.text().strip() == self.CONFIRM_TEXT
        )

    def _try_accept(self) -> None:
        if (
            self._all_pass
            and self.ack.isChecked()
            and self.confirm_line.text().strip() == self.CONFIRM_TEXT
        ):
            self.accept()
