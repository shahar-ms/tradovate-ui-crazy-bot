"""
Small always-on-top HUD with the essentials: mode, price, health, position,
last intent, last ack. Meant to live in an unused corner of Tradovate so the
operator can watch the bot without opening the full UI.

Behavior:
  - frameless + always-on-top + tool window (no taskbar entry, no focus steal)
  - draggable (click and hold anywhere on the HUD to move it)
  - right-click menu: show main window, reset position, close HUD
  - default position: middle-low on the left of the primary screen
    (~65% down from the top, 20px from the left edge)
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QPoint, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.theme import (BORDER, BROKEN_RED, DEGRADED_YELLOW, INACTIVE_GRAY, OK_GREEN,
                          PANEL, TEXT, TEXT_MUTED)
from app.ui.ui_state import UiState


HUD_WIDTH = 260
HUD_HEIGHT = 180
HUD_LEFT_MARGIN = 20
HUD_VERTICAL_PCT = 0.65   # 0.0 = top, 1.0 = bottom


def _status_for_health(state: str) -> str:
    return state if state in ("ok", "degraded", "broken") else "inactive"


class FloatingHud(QWidget):
    def __init__(self, signals: AppSignals, state: UiState,
                 parent: Optional[QWidget] = None):
        # No parent so the HUD is an independent top-level tool window.
        super().__init__(None)
        self.signals = signals
        self.state = state

        flags = (
            Qt.Tool                       # no taskbar entry
            | Qt.FramelessWindowHint      # no title bar
            | Qt.WindowStaysOnTopHint     # always on top
            | Qt.WindowDoesNotAcceptFocus # don't steal focus from Tradovate
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Tradovate bot HUD")
        self.setFixedSize(HUD_WIDTH, HUD_HEIGHT)

        self._drag_origin: Optional[QPoint] = None

        self._build_ui()
        self._apply_style()
        self._wire()

        # refresh from UiState periodically (in case signals are missed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._refresh_from_state)
        self._refresh_timer.start()
        self._refresh_from_state()

    # ---- layout ---- #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        # title row: "BOT" label + close button
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        title = QLabel("BOT")
        title.setStyleSheet("font-size: 10px; color: #8d97a5; letter-spacing: 1px;")
        row1.addWidget(title)
        self._mode_lbl = QLabel("DISCONNECTED")
        self._mode_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        row1.addWidget(self._mode_lbl)
        row1.addStretch(1)
        self._drag_hint = QLabel("⠿")
        self._drag_hint.setToolTip("Drag to move. Right-click for options.")
        self._drag_hint.setStyleSheet("color: #5a6371; font-size: 14px;")
        row1.addWidget(self._drag_hint)
        root.addLayout(row1)

        # price row (big)
        self._price_lbl = QLabel("—")
        self._price_lbl.setStyleSheet("font-size: 28px; font-weight: 700;")
        self._price_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self._price_lbl)

        # health + conf row
        row3 = QHBoxLayout()
        self._health_lbl = QLabel("health: inactive")
        self._health_lbl.setStyleSheet("font-size: 10px;")
        row3.addWidget(self._health_lbl)
        row3.addStretch(1)
        self._conf_lbl = QLabel("conf: —")
        self._conf_lbl.setStyleSheet("font-size: 10px; color: #8d97a5;")
        row3.addWidget(self._conf_lbl)
        root.addLayout(row3)

        # separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

        # position row
        self._pos_lbl = QLabel("pos: flat")
        self._pos_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
        root.addWidget(self._pos_lbl)

        # last intent / ack
        self._intent_lbl = QLabel("intent: —")
        self._intent_lbl.setStyleSheet("font-size: 10px; color: #8d97a5;")
        self._intent_lbl.setWordWrap(True)
        self._ack_lbl = QLabel("ack: —")
        self._ack_lbl.setStyleSheet("font-size: 10px; color: #8d97a5;")
        root.addWidget(self._intent_lbl)
        root.addWidget(self._ack_lbl)

        # halt banner (only shown when halted)
        self._halt_lbl = QLabel("")
        self._halt_lbl.setStyleSheet(
            f"background-color: {BROKEN_RED}; color: white; font-weight: 700; "
            "padding: 3px 6px; border-radius: 3px; font-size: 10px;"
        )
        self._halt_lbl.setWordWrap(True)
        self._halt_lbl.setVisible(False)
        root.addWidget(self._halt_lbl)

    def _apply_style(self) -> None:
        # Custom paint not needed — a rounded dark frame via stylesheet is enough.
        self.setStyleSheet(
            f"""
            FloatingHud {{
                background-color: {PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{ color: {TEXT}; }}
            """
        )

    def _wire(self) -> None:
        self.signals.mode_changed.connect(self._on_mode)
        self.signals.armed_changed.connect(lambda _a: self._refresh_from_state())
        self.signals.halt_triggered.connect(self._on_halt)
        self.signals.halt_cleared.connect(self._on_halt_cleared)
        self.signals.price_updated.connect(self._on_price)
        self.signals.health_updated.connect(self._on_health)
        self.signals.position_changed.connect(self._on_position)
        self.signals.signal_emitted.connect(self._on_intent)
        self.signals.execution_ack.connect(self._on_ack)

    # ---- positioning ---- #

    def place_default(self) -> None:
        """Middle-low height on the left edge of the primary screen."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        x = geom.left() + HUD_LEFT_MARGIN
        y = geom.top() + int(geom.height() * HUD_VERTICAL_PCT) - (HUD_HEIGHT // 2)
        # clamp so the HUD never ends up off-screen
        y = max(geom.top() + 10, min(y, geom.bottom() - HUD_HEIGHT - 10))
        self.move(x, y)

    # ---- event updates ---- #

    def _refresh_from_state(self) -> None:
        s = self.state
        self._mode_lbl.setText(s.mode)
        self._mode_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 12px; "
            f"color: {self._mode_color(s.mode, s.halted, s.armed)};"
        )
        self._price_lbl.setText(f"{s.last_price:.2f}" if s.last_price is not None else "—")
        self._health_lbl.setText(f"health: {s.price_stream_health}")
        self._health_lbl.setStyleSheet(
            f"font-size: 10px; color: {self._color_for_health(s.price_stream_health)};"
        )
        self._conf_lbl.setText(
            f"conf: {s.last_confidence:.0f}" if s.last_confidence else "conf: —"
        )
        pos_color = (OK_GREEN if s.position_side == "long"
                     else BROKEN_RED if s.position_side == "short"
                     else TEXT_MUTED)
        self._pos_lbl.setText(f"pos: {s.position_side}")
        self._pos_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {pos_color};")

        if s.last_intent_action:
            self._intent_lbl.setText(f"intent: {s.last_intent_action}")
        if s.last_ack_status:
            self._ack_lbl.setText(f"ack: {s.last_ack_status}")
            self._ack_lbl.setStyleSheet(
                f"font-size: 10px; color: {self._color_for_ack(s.last_ack_status)};"
            )

        if s.halted:
            self._halt_lbl.setText(f"HALTED — {s.halt_reason or '?'}")
            self._halt_lbl.setVisible(True)
        else:
            self._halt_lbl.setVisible(False)

    @staticmethod
    def _mode_color(mode: str, halted: bool, armed: bool) -> str:
        if halted or mode == "HALTED":
            return BROKEN_RED
        if armed or mode == "ARMED":
            return DEGRADED_YELLOW
        if mode in ("PAPER", "PRICE_DEBUG"):
            return OK_GREEN
        return INACTIVE_GRAY

    @staticmethod
    def _color_for_health(h: str) -> str:
        return {
            "ok": OK_GREEN,
            "degraded": DEGRADED_YELLOW,
            "broken": BROKEN_RED,
        }.get(h, INACTIVE_GRAY)

    @staticmethod
    def _color_for_ack(a: str) -> str:
        return {
            "ok": OK_GREEN,
            "unknown": DEGRADED_YELLOW,
            "failed": BROKEN_RED,
            "blocked": BROKEN_RED,
        }.get(a, TEXT_MUTED)

    @Slot(str)
    def _on_mode(self, _mode: str) -> None:
        self._refresh_from_state()

    @Slot(str)
    def _on_halt(self, _reason: str) -> None:
        self._refresh_from_state()

    @Slot()
    def _on_halt_cleared(self) -> None:
        self._refresh_from_state()

    @Slot(dict)
    def _on_price(self, _tick: dict) -> None:
        self._refresh_from_state()

    @Slot(dict)
    def _on_health(self, _health: dict) -> None:
        self._refresh_from_state()

    @Slot(str)
    def _on_position(self, _side: str) -> None:
        self._refresh_from_state()

    @Slot(dict)
    def _on_intent(self, _intent: dict) -> None:
        self._refresh_from_state()

    @Slot(dict)
    def _on_ack(self, _ack: dict) -> None:
        self._refresh_from_state()

    # ---- drag + context menu ---- #

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._drag_origin = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_origin)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag_origin = None

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)

        reset = QAction("Reset position", menu)
        reset.triggered.connect(self.place_default)
        menu.addAction(reset)

        show_main = QAction("Show main window", menu)
        show_main.triggered.connect(self._emit_show_main)
        menu.addAction(show_main)

        menu.addSeparator()

        close = QAction("Hide HUD", menu)
        close.triggered.connect(self.hide)
        menu.addAction(close)

        menu.exec(e.globalPos())

    def _emit_show_main(self) -> None:
        # Parent MainWindow listens for this via the signal below.
        from app.ui.app_signals import emit_event
        emit_event(self.signals, "info", "hud", "request show main window")
        # Also emit a dedicated signal; MainWindow wires it.
        self.signals.hud_show_main_requested.emit()
