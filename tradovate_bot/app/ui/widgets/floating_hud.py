"""
FloatingHud — the primary app surface. Always-on-top, frameless, draggable.

Rows (top → bottom):
  1. Title + mode badge + drag handle + close
  2. Big price
  3. Health + OCR confidence
  4. Position side @ entry price (with verified/— source tag)
  5. PnL (hidden when flat)
  6. BUY / SELL / CANCEL ALL
  7. ARM / DISARM / HALT / Setup
  8. Intent + ack (compact)
  9. Halt banner (collapsed when not halted)
  10. Toast label (briefly shown when a manual action is rejected)

State-driven enable/disable:
  - BUY/SELL enabled only in FLAT + not HALTED + not PENDING_*
  - CANCEL ALL enabled when running
  - ARM requires calibration loaded + not halted + currently disarmed
  - DISARM enabled when armed
  - HALT always enabled (while supervisor is running)
  - Setup always enabled (opens CalibrationDialog)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.theme import (ARM_ORANGE, BORDER, BROKEN_RED, CANCEL_YELLOW,
                          DEGRADED_YELLOW, INACTIVE_GRAY, OK_GREEN, PANEL,
                          PANEL_ALT, PRIMARY_BLUE, TEXT, TEXT_MUTED)
from app.ui.ui_state import UiState
from app.utils import paths

log = logging.getLogger(__name__)

HUD_WIDTH = 330
HUD_HEIGHT = 440
HUD_LEFT_MARGIN = 20
HUD_VERTICAL_PCT = 0.55   # center-ish of the left edge

POSITION_FILE = "hud_pos.json"


class FloatingHud(QWidget):
    # emitted when the Setup button is clicked
    setup_requested = Signal()

    def __init__(self, signals: AppSignals, state: UiState,
                 controller=None, parent: Optional[QWidget] = None):
        super().__init__(None)
        self.signals = signals
        self.state = state
        self.controller = controller

        flags = (
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
        )
        self.setWindowFlags(flags)
        self.setWindowTitle("Tradovate bot")
        self.setFixedSize(HUD_WIDTH, HUD_HEIGHT)

        self._drag_origin: Optional[QPoint] = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast_lbl.setText(""))

        self._build_ui()
        self._apply_style()
        self._wire()
        self._refresh_all()

        # refresh from UiState every 400ms
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._refresh_all)
        self._refresh_timer.start()

    # ---- layout ---- #

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # row 1: title + mode + drag + close
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        title = QLabel("BOT")
        title.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 1px;")
        row1.addWidget(title)

        self._mode_lbl = QLabel("DISCONNECTED")
        self._mode_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        row1.addWidget(self._mode_lbl)
        row1.addStretch(1)

        self._drag_hint = QLabel("⠿")
        self._drag_hint.setToolTip("Drag to move. Right-click for more.")
        self._drag_hint.setStyleSheet(f"color: {INACTIVE_GRAY}; font-size: 14px;")
        row1.addWidget(self._drag_hint)

        self._close_btn = QLabel("✕")
        self._close_btn.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 14px; padding-left: 6px;"
        )
        self._close_btn.setToolTip("Exit the bot")
        self._close_btn.mousePressEvent = lambda e: self.close()  # type: ignore[assignment]
        row1.addWidget(self._close_btn)
        root.addLayout(row1)

        # row 2: big price
        self._price_lbl = QLabel("—")
        self._price_lbl.setAlignment(Qt.AlignCenter)
        self._price_lbl.setStyleSheet("font-size: 30px; font-weight: 700;")
        root.addWidget(self._price_lbl)

        # row 3: health + confidence
        row3 = QHBoxLayout()
        self._health_lbl = QLabel("health: inactive")
        self._health_lbl.setStyleSheet("font-size: 10px;")
        row3.addWidget(self._health_lbl)
        row3.addStretch(1)
        self._conf_lbl = QLabel("conf: —")
        self._conf_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        row3.addWidget(self._conf_lbl)
        root.addLayout(row3)

        # separator
        root.addWidget(self._sep())

        # row 4 / 5: position + PnL
        self._pos_lbl = QLabel("pos: flat")
        self._pos_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        root.addWidget(self._pos_lbl)
        self._pnl_lbl = QLabel("PnL: —")
        self._pnl_lbl.setStyleSheet("font-size: 12px;")
        root.addWidget(self._pnl_lbl)

        # separator
        root.addWidget(self._sep())

        # row 6: BUY / SELL / CANCEL ALL
        row6 = QHBoxLayout()
        row6.setSpacing(4)
        self._buy_btn = self._make_button("BUY", role="primary")
        self._sell_btn = self._make_button("SELL", role="danger")
        self._cancel_btn = self._make_button("CANCEL ALL", role="cancel")
        row6.addWidget(self._buy_btn)
        row6.addWidget(self._sell_btn)
        row6.addWidget(self._cancel_btn, 1)
        root.addLayout(row6)

        # row 7: ARM / DISARM / HALT / Setup
        row7 = QHBoxLayout()
        row7.setSpacing(4)
        self._arm_btn = self._make_button("ARM", role="arm", small=True)
        self._disarm_btn = self._make_button("DISARM", small=True)
        self._halt_btn = self._make_button("HALT", role="halt", small=True)
        self._setup_btn = self._make_button("Setup", small=True)
        row7.addWidget(self._arm_btn)
        row7.addWidget(self._disarm_btn)
        row7.addWidget(self._halt_btn)
        row7.addWidget(self._setup_btn)
        root.addLayout(row7)

        # separator
        root.addWidget(self._sep())

        # row 8: intent + ack
        self._intent_lbl = QLabel("intent: —")
        self._intent_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        self._intent_lbl.setWordWrap(True)
        root.addWidget(self._intent_lbl)
        self._ack_lbl = QLabel("ack: —")
        self._ack_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        root.addWidget(self._ack_lbl)

        # paused banner (yellow — transient, auto-recovers)
        self._paused_lbl = QLabel("")
        self._paused_lbl.setStyleSheet(
            f"background-color: {DEGRADED_YELLOW}; color: #101010; font-weight: 700; "
            "padding: 4px 8px; border-radius: 3px; font-size: 10px;"
        )
        self._paused_lbl.setWordWrap(True)
        self._paused_lbl.setVisible(False)
        root.addWidget(self._paused_lbl)

        # halt banner (red — unrecoverable, operator must act)
        self._halt_lbl = QLabel("")
        self._halt_lbl.setStyleSheet(
            f"background-color: {BROKEN_RED}; color: white; font-weight: 700; "
            "padding: 4px 8px; border-radius: 3px; font-size: 10px;"
        )
        self._halt_lbl.setWordWrap(True)
        self._halt_lbl.setVisible(False)
        root.addWidget(self._halt_lbl)

        # toast (for rejections)
        self._toast_lbl = QLabel("")
        self._toast_lbl.setStyleSheet(
            f"background-color: {DEGRADED_YELLOW}; color: #101010; "
            "padding: 4px 8px; border-radius: 3px; font-size: 10px; font-weight: 600;"
        )
        self._toast_lbl.setWordWrap(True)
        self._toast_lbl.setVisible(False)
        root.addWidget(self._toast_lbl)

        root.addStretch(1)

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        return sep

    def _make_button(self, text: str, role: str = "", small: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)  # never steal focus from Tradovate
        b.setMinimumHeight(26 if small else 34)
        if role:
            b.setProperty("role", role)
        return b

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            FloatingHud {{
                background-color: {PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{ color: {TEXT}; }}
            QPushButton {{
                background-color: {PANEL_ALT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT};
                padding: 4px 6px;
            }}
            QPushButton:hover    {{ background-color: #2c3541; }}
            QPushButton:disabled {{ color: {INACTIVE_GRAY}; background-color: #1a1e25; }}
            QPushButton[role="primary"] {{ background-color: {OK_GREEN}; color: #0b0b0b; font-weight: 700; border: none; }}
            QPushButton[role="danger"]  {{ background-color: {BROKEN_RED}; color: white; font-weight: 700; border: none; }}
            QPushButton[role="cancel"]  {{ background-color: {CANCEL_YELLOW}; color: #101010; font-weight: 700; border: none; }}
            QPushButton[role="arm"]     {{ background-color: {ARM_ORANGE}; color: #101010; font-weight: 700; border: none; }}
            QPushButton[role="halt"]    {{ background-color: {BROKEN_RED}; color: white; font-weight: 700; border: none; }}
            """
        )

    def _wire(self) -> None:
        # button clicks
        self._buy_btn.clicked.connect(self._on_buy)
        self._sell_btn.clicked.connect(self._on_sell)
        self._cancel_btn.clicked.connect(self._on_cancel_all)
        self._arm_btn.clicked.connect(self._on_arm)
        self._disarm_btn.clicked.connect(self._on_disarm)
        self._halt_btn.clicked.connect(self._on_halt)
        self._setup_btn.clicked.connect(self.setup_requested.emit)

        # signals from the bus
        self.signals.price_updated.connect(lambda _t: self._refresh_all())
        self.signals.health_updated.connect(lambda _h: self._refresh_all())
        self.signals.mode_changed.connect(lambda _m: self._refresh_all())
        self.signals.armed_changed.connect(lambda _a: self._refresh_all())
        self.signals.halt_triggered.connect(lambda _r: self._refresh_all())
        self.signals.halt_cleared.connect(self._refresh_all)
        self.signals.position_changed.connect(lambda _s: self._refresh_all())
        self.signals.signal_emitted.connect(lambda _i: self._refresh_all())
        self.signals.execution_ack.connect(lambda _a: self._refresh_all())
        self.signals.manual_rejected.connect(self._show_toast)

    # ---- positioning (persist across runs) ---- #

    def place_default(self) -> None:
        # try saved first
        if self._restore_saved_position():
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        x = geom.left() + HUD_LEFT_MARGIN
        y = geom.top() + int(geom.height() * HUD_VERTICAL_PCT) - HUD_HEIGHT // 2
        y = max(geom.top() + 10, min(y, geom.bottom() - HUD_HEIGHT - 10))
        self.move(x, y)

    def _position_path(self) -> Path:
        return paths.state_dir() / POSITION_FILE

    def _restore_saved_position(self) -> bool:
        p = self._position_path()
        if not p.exists():
            return False
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            x, y = int(d["x"]), int(d["y"])
            # clamp to current virtual screen
            screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                x = max(g.left(), min(x, g.right() - HUD_WIDTH))
                y = max(g.top(), min(y, g.bottom() - HUD_HEIGHT))
            self.move(x, y)
            return True
        except Exception:
            return False

    def save_position(self) -> None:
        p = self._position_path()
        try:
            p.write_text(json.dumps({"x": self.x(), "y": self.y()}), encoding="utf-8")
        except Exception:
            log.debug("failed to save HUD position", exc_info=True)

    # ---- refresh ---- #

    def _refresh_all(self) -> None:
        s = self.state
        # mode + color
        self._mode_lbl.setText(s.mode)
        self._mode_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 12px; "
            f"color: {self._mode_color(s)};"
        )

        # price
        self._price_lbl.setText(f"{s.last_price:.2f}" if s.last_price is not None else "—")

        # health + conf
        self._health_lbl.setText(f"health: {s.price_stream_health}")
        self._health_lbl.setStyleSheet(
            f"font-size: 10px; color: {self._health_color(s.price_stream_health)};"
        )
        self._conf_lbl.setText(f"conf: {s.last_confidence:.0f}"
                               if s.last_confidence else "conf: —")

        # position
        if s.position_side == "flat":
            self._pos_lbl.setText("pos: flat")
            self._pos_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_MUTED};")
            self._pnl_lbl.setVisible(False)
        else:
            entry_txt = f"{s.entry_price:.2f}" if s.entry_price is not None else "—"
            src = s.fill_price_source
            tag = "(verified)" if src == "position_ocr" else "(—)" if src is None else f"({src})"
            color = OK_GREEN if s.position_side == "long" else BROKEN_RED
            self._pos_lbl.setText(f"pos: {s.position_side.upper()} @ {entry_txt} {tag}")
            self._pos_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")

            # PnL row
            self._pnl_lbl.setVisible(True)
            if s.pnl_points is None:
                self._pnl_lbl.setText("PnL: —  ⚠")
                self._pnl_lbl.setStyleSheet(f"font-size: 12px; color: {TEXT_MUTED};")
                self._pnl_lbl.setToolTip(
                    "No verified broker fill. Calibrate the Position region for accurate PnL."
                )
            else:
                pts = s.pnl_points
                usd = s.pnl_usd or 0.0
                sign_color = OK_GREEN if pts >= 0 else BROKEN_RED
                self._pnl_lbl.setText(
                    f"PnL: {pts:+.2f} pts   {usd:+.2f} USD"
                )
                self._pnl_lbl.setStyleSheet(
                    f"font-size: 12px; font-weight: 600; color: {sign_color};"
                )
                self._pnl_lbl.setToolTip("")

        # intent + ack
        intent_parts = []
        if s.last_intent_action:
            intent_parts.append(s.last_intent_action)
        if s.last_intent_reason:
            intent_parts.append(f"({s.last_intent_reason})")
        self._intent_lbl.setText(f"intent: {' '.join(intent_parts) or '—'}")
        ack_parts = [f"ack: {s.last_ack_status or '—'}"]
        if s.fill_price is not None:
            ack_parts.append(f"fill={s.fill_price:.2f}")
        self._ack_lbl.setText("  ".join(ack_parts))

        # paused banner (yellow, distinct from red halt)
        if s.paused and not s.halted:
            self._paused_lbl.setText(f"PAUSED — {s.pause_reason or 'screen not visible'}")
            self._paused_lbl.setVisible(True)
        else:
            self._paused_lbl.setVisible(False)

        # halt banner (red)
        if s.halted:
            self._halt_lbl.setText(f"HALTED — {s.halt_reason or '?'}")
            self._halt_lbl.setVisible(True)
        else:
            self._halt_lbl.setVisible(False)

        # button enablement
        running = bool(self.controller and self.controller.is_running())
        flat = s.position_side == "flat"
        halted = s.halted
        paused = s.paused
        armed = s.armed

        # Entry buttons: require running + flat + not halted + not paused
        self._buy_btn.setEnabled(running and flat and not halted and not paused)
        self._sell_btn.setEnabled(running and flat and not halted and not paused)
        # CANCEL ALL still works while paused (it's a safety action)
        self._cancel_btn.setEnabled(running and not halted)
        # ARM requires calibration + not paused + not halted + not already armed
        self._arm_btn.setEnabled(
            running and not armed and not halted and not paused and s.calibration_loaded
        )
        self._disarm_btn.setEnabled(running and armed)
        self._halt_btn.setEnabled(running)
        self._setup_btn.setEnabled(True)

    @staticmethod
    def _mode_color(s: UiState) -> str:
        if s.halted or s.mode == "HALTED":
            return BROKEN_RED
        if s.paused:
            return DEGRADED_YELLOW
        if s.armed or s.mode == "ARMED":
            return DEGRADED_YELLOW
        if s.mode in ("PAPER", "PRICE_DEBUG"):
            return OK_GREEN
        return INACTIVE_GRAY

    @staticmethod
    def _health_color(h: str) -> str:
        return {"ok": OK_GREEN, "degraded": DEGRADED_YELLOW, "broken": BROKEN_RED}.get(
            h, INACTIVE_GRAY
        )

    # ---- button handlers ---- #

    def _on_buy(self) -> None:
        if self.controller:
            self.controller.submit_manual("BUY")

    def _on_sell(self) -> None:
        if self.controller:
            self.controller.submit_manual("SELL")

    def _on_cancel_all(self) -> None:
        if self.controller:
            self.controller.cancel_all()

    def _on_arm(self) -> None:
        # delegate to the ArmConfirmDialog the existing code already has
        from app.ui.dialogs.arm_confirm_dialog import ArmConfirmDialog
        if self.controller is None:
            return
        dlg = ArmConfirmDialog(self.controller, self.state, self)
        if dlg.exec():
            err = self.controller.arm()
            if err:
                self._show_toast(err)

    def _on_disarm(self) -> None:
        if self.controller:
            self.controller.disarm()

    def _on_halt(self) -> None:
        if self.controller:
            self.controller.halt("operator_halt")

    @Slot(str)
    def _show_toast(self, message: str) -> None:
        self._toast_lbl.setText(message[:120])
        self._toast_lbl.setVisible(True)
        self._toast_timer.start(2500)

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
        if self._drag_origin is not None:
            self._drag_origin = None
            self.save_position()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)

        reset = QAction("Reset position", menu)
        reset.triggered.connect(lambda: (self.place_default(), self.save_position()))
        menu.addAction(reset)

        setup = QAction("Open Calibration (Setup)", menu)
        setup.triggered.connect(self.setup_requested.emit)
        menu.addAction(setup)

        open_logs = QAction("Open log folder", menu)
        open_logs.triggered.connect(self._open_logs_folder)
        menu.addAction(open_logs)

        menu.addSeparator()

        exit_act = QAction("Exit app", menu)
        exit_act.triggered.connect(self.close)
        menu.addAction(exit_act)

        menu.exec(e.globalPos())

    def _open_logs_folder(self) -> None:
        p = paths.logs_dir()
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(p)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception:
            log.warning("failed to open logs folder", exc_info=True)

    # ---- lifecycle ---- #

    def closeEvent(self, event):  # noqa: N802
        self.save_position()
        super().closeEvent(event)
