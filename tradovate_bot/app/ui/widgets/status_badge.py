"""Small colored pill for runtime mode / health."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class StatusBadge(QLabel):
    STATE_MAP = {
        "ok": "ok",
        "running": "ok",
        "PAPER": "ok",
        "PRICE_DEBUG": "ok",
        "connected": "ok",
        "degraded": "degraded",
        "warn": "degraded",
        "ARMED": "degraded",      # armed is risky — yellow
        "broken": "broken",
        "HALTED": "broken",
        "error": "broken",
        "stopped": "inactive",
        "DISCONNECTED": "inactive",
        "CALIBRATION": "inactive",
    }

    def __init__(self, initial: str = "inactive", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(100)
        self.set_state(initial)

    def set_state(self, state: str) -> None:
        self.setText(state)
        mapped = self.STATE_MAP.get(state, "inactive")
        self.setProperty("status", mapped)
        self.style().unpolish(self)
        self.style().polish(self)
