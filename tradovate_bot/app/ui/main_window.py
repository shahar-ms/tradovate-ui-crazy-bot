"""
Main window shell: top status bar, left nav, stacked pages, bottom
emergency strip. The window stays the same; pages swap in the center.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.ui_state import UiState
from app.ui.widgets.emergency_strip import EmergencyStrip
from app.ui.widgets.top_bar import TopBar

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, signals: AppSignals, state: UiState, controller: UiController,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.state = state
        self.controller = controller

        self.setWindowTitle("Tradovate UI bot — control panel")
        self.resize(1280, 820)

        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # top bar
        self.top_bar = TopBar()
        outer.addWidget(self.top_bar)

        # body: nav + pages
        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setProperty("role", "nav")
        self.nav.setFixedWidth(170)
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        # emergency strip
        self.emergency = EmergencyStrip()
        outer.addWidget(self.emergency)
        self.emergency.disarm_clicked.connect(self._on_disarm)
        self.emergency.cancel_all_clicked.connect(self._on_cancel_all)
        self.emergency.halt_clicked.connect(self._on_halt)

        self.setCentralWidget(central)

        # keyboard shortcut: Ctrl+H halts from anywhere
        halt_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        halt_shortcut.activated.connect(self._on_halt)

        # signal wiring
        self.signals.mode_changed.connect(self._on_mode_changed)
        self.signals.armed_changed.connect(self._on_armed_changed)
        self.signals.halt_triggered.connect(self._on_halt_triggered)
        self.signals.health_updated.connect(self._on_health_updated)
        self.signals.anchor_guard_changed.connect(self._on_anchor_changed)
        self.signals.controller_state_changed.connect(self._on_controller_state)

        self._update_top_from_state()

    # ---- navigation ---- #

    def add_page(self, name: str, page: QWidget) -> int:
        idx = self.stack.addWidget(page)
        item = QListWidgetItem(name)
        self.nav.addItem(item)
        if idx == 0:
            self.nav.setCurrentRow(0)
        return idx

    def go_to(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.nav.setCurrentRow(index)

    # ---- slot handlers ---- #

    @Slot(str)
    def _on_mode_changed(self, mode: str) -> None:
        self.top_bar.set_mode(mode)
        self._update_emergency_meta()

    @Slot(bool)
    def _on_armed_changed(self, armed: bool) -> None:
        self.top_bar.set_armed(armed, halted=self.state.halted)
        self._update_emergency_meta()

    @Slot(str)
    def _on_halt_triggered(self, reason: str) -> None:
        self.top_bar.set_armed(self.state.armed, halted=True)
        self._update_emergency_meta()
        QMessageBox.warning(self, "HALTED",
                            f"The bot has halted.\n\nReason: {reason}\n\n"
                            "Investigate before resuming.")

    @Slot(dict)
    def _on_health_updated(self, payload: dict) -> None:
        self.top_bar.set_price_health(payload.get("health_state", "inactive"))

    @Slot(bool, float)
    def _on_anchor_changed(self, ok: bool, _sim: float) -> None:
        self.top_bar.set_anchor(ok)

    @Slot(str)
    def _on_controller_state(self, state: str) -> None:
        if state == "running":
            self.top_bar.set_session(self.state.session_id)
        elif state == "stopped":
            self.top_bar.set_session("—")

    # ---- emergency strip actions ---- #

    def _on_disarm(self) -> None:
        self.controller.disarm()

    def _on_cancel_all(self) -> None:
        if QMessageBox.question(self, "Cancel All?",
                                "Send CANCEL ALL to the Tradovate screen?\n\n"
                                "This clicks the calibrated cancel-all button.",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.controller.cancel_all()

    def _on_halt(self) -> None:
        if QMessageBox.question(self, "Halt?",
                                "Halt the bot? No new entries will be sent until "
                                "you resume manually.",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.controller.halt("operator_halt")

    # ---- internals ---- #

    def _update_emergency_meta(self) -> None:
        parts = [
            f"mode={self.state.mode}",
            f"armed={self.state.armed}",
        ]
        if self.state.halted:
            parts.append(f"HALTED ({self.state.halt_reason or '?'})")
        self.emergency.set_meta("  |  ".join(parts))

    def _update_top_from_state(self) -> None:
        self.top_bar.set_session(self.state.session_id)
        self.top_bar.set_mode(self.state.mode)
        self.top_bar.set_price_health(self.state.price_stream_health)
        self.top_bar.set_anchor(self.state.anchor_ok)
        self.top_bar.set_armed(self.state.armed, halted=self.state.halted)
        self._update_emergency_meta()

    # ---- lifecycle ---- #

    def closeEvent(self, event):  # noqa: N802
        try:
            self.controller.stop()
        except Exception:
            log.exception("controller.stop raised on close")
        super().closeEvent(event)
