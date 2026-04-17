"""
Top status bar: always-visible session / mode / health badges.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.ui.widgets.status_badge import StatusBadge


class TopBar(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setFixedHeight(50)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(16)

        title = QLabel("Tradovate UI bot")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        lay.addWidget(title)

        self._session = QLabel("session: —")
        self._session.setProperty("role", "muted")
        lay.addWidget(self._session)

        lay.addStretch(1)

        lay.addWidget(QLabel("mode:"))
        self.mode_badge = StatusBadge("DISCONNECTED")
        lay.addWidget(self.mode_badge)

        lay.addWidget(QLabel("price:"))
        self.price_health_badge = StatusBadge("inactive")
        lay.addWidget(self.price_health_badge)

        lay.addWidget(QLabel("anchor:"))
        self.anchor_badge = StatusBadge("inactive")
        lay.addWidget(self.anchor_badge)

        lay.addWidget(QLabel("armed:"))
        self.armed_badge = StatusBadge("inactive")
        lay.addWidget(self.armed_badge)

    def set_session(self, session_id: str) -> None:
        self._session.setText(f"session: {session_id or '—'}")

    def set_mode(self, mode: str) -> None:
        self.mode_badge.set_state(mode)

    def set_price_health(self, health: str) -> None:
        self.price_health_badge.set_state(health)

    def set_anchor(self, ok: bool) -> None:
        self.anchor_badge.set_state("ok" if ok else "broken")

    def set_armed(self, armed: bool, halted: bool = False) -> None:
        if halted:
            self.armed_badge.set_state("HALTED")
        elif armed:
            self.armed_badge.set_state("ARMED")
        else:
            self.armed_badge.set_state("inactive")
