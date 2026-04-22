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
                               QStackedWidget, QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.theme import (ARM_ORANGE, BORDER, BROKEN_RED, CANCEL_YELLOW,
                          DEGRADED_YELLOW, INACTIVE_GRAY, OK_GREEN, PANEL,
                          PANEL_ALT, PRIMARY_BLUE, TEXT, TEXT_MUTED)
from app.ui.ui_state import UiState
from app.utils import paths

log = logging.getLogger(__name__)

HUD_WIDTH = 330
HUD_HEIGHT = 440
HUD_COMPACT_WIDTH = 320
HUD_COMPACT_HEIGHT = 46
HUD_LEFT_MARGIN = 20
# Fraction of the available screen height at which the HUD's TOP edge
# is anchored by default. The HUD extends downward from there. 0.35
# puts the HUD top ~1/3 of the way down the screen, below a typical
# Tradovate header but still well above the bottom.
HUD_VERTICAL_PCT = 0.35

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
        # size is switched between expanded and compact via _set_minimized
        self._minimized: bool = False
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
        # outer layout: a single stacked widget swapping between expanded + compact
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._expanded_page = QWidget()
        self._build_expanded_page(self._expanded_page)
        self._stack.addWidget(self._expanded_page)

        self._compact_page = QWidget()
        self._build_compact_page(self._compact_page)
        self._stack.addWidget(self._compact_page)

        self._stack.setCurrentIndex(0)   # expanded by default

    def _build_expanded_page(self, page: QWidget) -> None:
        root = QVBoxLayout(page)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # row 1: title + mode + drag + minimize (no close X — exit via right-click menu)
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        title = QLabel("BOT")
        title.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 1px;")
        row1.addWidget(title)

        self._mode_lbl = QLabel("DISCONNECTED")
        self._mode_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        row1.addWidget(self._mode_lbl)
        row1.addStretch(1)

        # Calibration status chip — green ✓ when a valid screen_map is loaded,
        # red ✗ with a tooltip prompting Setup when it's missing/invalid.
        self._cal_lbl = QLabel("CAL: ?")
        self._cal_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 700; "
            f"padding: 1px 6px; border-radius: 3px; "
            f"background-color: {PANEL_ALT}; color: {TEXT_MUTED};"
        )
        row1.addWidget(self._cal_lbl)

        self._drag_hint = QLabel("⠿")
        self._drag_hint.setToolTip("Drag to move. Right-click for more.")
        self._drag_hint.setStyleSheet(f"color: {INACTIVE_GRAY}; font-size: 14px;")
        row1.addWidget(self._drag_hint)

        self._minimize_btn = QLabel("−")
        self._minimize_btn.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 18px; font-weight: 700; padding: 0 6px;"
        )
        self._minimize_btn.setToolTip("Minimize to compact block. Click the block to expand again.")
        self._minimize_btn.mousePressEvent = lambda e: self._set_minimized(True)  # type: ignore[assignment]
        row1.addWidget(self._minimize_btn)
        root.addLayout(row1)

        # row 2: big price
        self._price_lbl = QLabel("—")
        self._price_lbl.setAlignment(Qt.AlignCenter)
        self._price_lbl.setStyleSheet("font-size: 30px; font-weight: 700;")
        root.addWidget(self._price_lbl)

        # row 3: health + confidence + per-frame timing (end-to-end)
        row3 = QHBoxLayout()
        self._health_lbl = QLabel("health: inactive")
        self._health_lbl.setStyleSheet("font-size: 10px;")
        row3.addWidget(self._health_lbl)
        row3.addStretch(1)
        self._conf_lbl = QLabel("conf: —")
        self._conf_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        row3.addWidget(self._conf_lbl)
        self._frame_ms_lbl = QLabel("— ms")
        self._frame_ms_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")
        self._frame_ms_lbl.setToolTip(
            "End-to-end client latency: time from grabbing the price region "
            "to the new tick being available. Includes screen capture + "
            "dedup check + OCR (when needed) + publish."
        )
        row3.addWidget(self._frame_ms_lbl)
        root.addLayout(row3)

        # separator
        root.addWidget(self._sep())

        # bot-state row: ENABLED / DISABLED + single toggle button.
        #   ENABLED  = armed (live clicks) + strategy auto-trades
        #   DISABLED = manual buttons + OCR only; strategy silent
        bot_row = QHBoxLayout()
        bot_row.setSpacing(6)
        self._bot_state_lbl = QLabel("BOT: —")
        self._bot_state_lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        bot_row.addWidget(self._bot_state_lbl)
        bot_row.addStretch(1)
        self._bot_toggle_btn = self._make_button("Enable Bot", role="arm", small=True)
        self._bot_toggle_btn.setMinimumHeight(30)
        bot_row.addWidget(self._bot_toggle_btn)
        root.addLayout(bot_row)

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

        # row 7: HALT + Setup (secondary controls — the main ON/OFF toggle
        # replaces the old ARM/DISARM/ENABLE/DISABLE quartet). The arm-
        # confirmation dialog still runs when turning ON.
        row7 = QHBoxLayout()
        row7.setSpacing(4)
        self._halt_btn = self._make_button("HALT", role="halt", small=True)
        self._setup_btn = self._make_button("Setup", small=True)
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

    def _build_compact_page(self, page: QWidget) -> None:
        """
        Small-but-noticeable block shown when the HUD is minimized.
        One horizontal row: [status dot] MODE  PRICE  pos  [+]
        Click anywhere on the block to expand back.
        """
        root = QHBoxLayout(page)
        root.setContentsMargins(10, 4, 10, 4)
        root.setSpacing(8)

        self._compact_dot = QLabel("●")
        self._compact_dot.setStyleSheet(
            f"color: {INACTIVE_GRAY}; font-size: 18px;"
        )
        root.addWidget(self._compact_dot)

        self._compact_mode = QLabel("BOT")
        self._compact_mode.setStyleSheet(
            "font-weight: 700; font-size: 11px; letter-spacing: 1px;"
        )
        root.addWidget(self._compact_mode)

        self._compact_price = QLabel("—")
        self._compact_price.setStyleSheet("font-size: 18px; font-weight: 700;")
        self._compact_price.setAlignment(Qt.AlignCenter)
        # guarantee room for the full price string (e.g. "26680.00" + wider
        # digits) so it's never truncated when the window is fixed-sized.
        self._compact_price.setMinimumWidth(110)
        root.addWidget(self._compact_price, 1)

        self._compact_pos = QLabel("flat")
        self._compact_pos.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self._compact_pos)

        self._expand_btn = QLabel("⛶")
        self._expand_btn.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 16px; font-weight: 700; padding: 0 4px;"
        )
        self._expand_btn.setToolTip("Expand HUD")
        self._expand_btn.mousePressEvent = lambda e: self._set_minimized(False)  # type: ignore[assignment]
        root.addWidget(self._expand_btn)
        # double-click anywhere on the block = expand
        page.mouseDoubleClickEvent = lambda e: self._set_minimized(False)  # type: ignore[assignment]

    def _set_minimized(self, minimized: bool) -> None:
        """Swap between expanded (full HUD) and compact (small block) views."""
        if minimized == self._minimized:
            return
        self._minimized = minimized
        self._stack.setCurrentIndex(1 if minimized else 0)
        if minimized:
            self.setFixedSize(HUD_COMPACT_WIDTH, HUD_COMPACT_HEIGHT)
        else:
            self.setFixedSize(HUD_WIDTH, HUD_HEIGHT)
        # refresh so the compact row reflects current state immediately
        self._refresh_all()
        # clamp back on-screen in case the new size would push it off
        self._clamp_to_screen()
        self.save_position()

    def _clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.frameGeometry().topLeft()) \
                 or QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        x = max(g.left(), min(self.x(), g.right() - self.width()))
        y = max(g.top(), min(self.y(), g.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

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
        self._halt_btn.clicked.connect(self._on_halt)
        self._setup_btn.clicked.connect(self.setup_requested.emit)
        self._bot_toggle_btn.clicked.connect(self._on_bot_toggle)

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

    def place_default(self, use_saved: bool = True) -> None:
        """Place the HUD. By default, tries the last-saved position first.
        Pass `use_saved=False` to force the computed default (used by the
        context menu's 'Reset position' action)."""
        if use_saved and self._restore_saved_position():
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        x = geom.left() + HUD_LEFT_MARGIN
        # Anchor the HUD's TOP edge at HUD_VERTICAL_PCT of the screen (so the
        # whole HUD body sits BELOW that line, not centered on it).
        y = geom.top() + int(geom.height() * HUD_VERTICAL_PCT)
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
            # restore minimized state first so size is correct when clamping
            if bool(d.get("minimized", False)):
                self._set_minimized(True)
            w = self.width()
            h = self.height()
            # clamp to current virtual screen
            screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                x = max(g.left(), min(x, g.right() - w))
                y = max(g.top(), min(y, g.bottom() - h))
            self.move(x, y)
            return True
        except Exception:
            return False

    def save_position(self) -> None:
        p = self._position_path()
        try:
            p.write_text(
                json.dumps({"x": self.x(), "y": self.y(),
                            "minimized": self._minimized}),
                encoding="utf-8",
            )
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

        # calibration status chip
        if s.calibration_loaded:
            self._cal_lbl.setText("CAL ✓")
            self._cal_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; padding: 1px 6px; "
                f"border-radius: 3px; background-color: {OK_GREEN}; color: #0b0b0b;"
            )
            self._cal_lbl.setToolTip("Calibration loaded — screen_map.json is valid.")
        else:
            self._cal_lbl.setText("CAL ✗")
            self._cal_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; padding: 1px 6px; "
                f"border-radius: 3px; background-color: {BROKEN_RED}; color: white;"
            )
            self._cal_lbl.setToolTip(
                "No valid calibration. Click Setup to calibrate the price "
                "and position regions."
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

        # per-frame end-to-end latency. Color-code: green under 60ms, yellow
        # 60-150ms, red above 150ms — roughly matches human-noticeable tiers.
        if s.last_frame_ms > 0:
            ms = s.last_frame_ms
            if ms < 60:
                ms_color = OK_GREEN
            elif ms < 150:
                ms_color = DEGRADED_YELLOW
            else:
                ms_color = BROKEN_RED
            self._frame_ms_lbl.setText(f"{ms:.0f} ms")
            self._frame_ms_lbl.setStyleSheet(
                f"font-size: 10px; color: {ms_color}; font-weight: 600;"
            )
        else:
            self._frame_ms_lbl.setText("— ms")
            self._frame_ms_lbl.setStyleSheet(f"font-size: 10px; color: {TEXT_MUTED};")

        # position
        size_txt = f"  size: {s.position_size}" if s.position_size is not None else ""
        if s.position_side == "flat":
            self._pos_lbl.setText(f"pos: flat{size_txt}")
            self._pos_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_MUTED};")
            self._pnl_lbl.setVisible(False)
        else:
            entry_txt = f"{s.entry_price:.2f}" if s.entry_price is not None else "—"
            src = s.fill_price_source
            tag = "(verified)" if src == "position_ocr" else "(—)" if src is None else f"({src})"
            color = OK_GREEN if s.position_side == "long" else BROKEN_RED
            self._pos_lbl.setText(
                f"pos: {s.position_side.upper()} @ {entry_txt}{size_txt} {tag}"
            )
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

        # Entry buttons: require running + armed (else clicks are dry-run and
        # never reach Tradovate) + flat + not halted + not paused.
        # Hint when disabled so the operator knows why.
        entry_ok = running and armed and flat and not halted and not paused
        self._buy_btn.setEnabled(entry_ok)
        self._sell_btn.setEnabled(entry_ok)
        if not armed:
            self._buy_btn.setToolTip("Enable Bot first — clicks only reach Tradovate when armed.")
            self._sell_btn.setToolTip("Enable Bot first — clicks only reach Tradovate when armed.")
        else:
            self._buy_btn.setToolTip("")
            self._sell_btn.setToolTip("")
        # CANCEL ALL is a safety action but also needs armed to click live.
        self._cancel_btn.setEnabled(running and armed and not halted)
        if not armed:
            self._cancel_btn.setToolTip(
                "Enable Bot first — CANCEL ALL needs armed execution to reach Tradovate."
            )
        else:
            self._cancel_btn.setToolTip("")
        self._halt_btn.setEnabled(running)
        self._setup_btn.setEnabled(True)

        # --- BOT single power toggle: ON = armed + auto; OFF = neither --- #
        bot_on = bool(armed and s.auto_enabled)
        if halted:
            bot_text, bot_color, btn_text, btn_role = (
                "BOT: HALTED", BROKEN_RED, "Halted", "danger",
            )
        elif paused:
            bot_text, bot_color, btn_text, btn_role = (
                "BOT: PAUSED", DEGRADED_YELLOW,
                "Turn OFF" if bot_on else "Turn ON",
                "danger" if bot_on else "arm",
            )
        elif bot_on:
            bot_text, bot_color, btn_text, btn_role = (
                "BOT: ON", OK_GREEN, "Turn OFF", "danger",
            )
        else:
            bot_text, bot_color, btn_text, btn_role = (
                "BOT: OFF", INACTIVE_GRAY, "Turn ON", "arm",
            )
        self._bot_state_lbl.setText(bot_text)
        self._bot_state_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {bot_color};"
        )
        self._bot_toggle_btn.setText(btn_text)
        self._bot_toggle_btn.setProperty("role", btn_role)
        self._bot_toggle_btn.style().unpolish(self._bot_toggle_btn)
        self._bot_toggle_btn.style().polish(self._bot_toggle_btn)
        self._bot_toggle_btn.setEnabled(running and not halted)

        # --- keep the compact view in sync too --- #
        self._refresh_compact(s)

    def _refresh_compact(self, s: UiState) -> None:
        """Keep the minimized block's labels up to date."""
        # color dot: red if halted or calibration missing, yellow if paused,
        # green if enabled+armed, gray if disabled.
        # No-calibration takes priority over the running-state colors because
        # nothing the bot reports is trustworthy without it.
        if not s.calibration_loaded:
            dot_color = BROKEN_RED
            self._compact_dot.setToolTip("No calibration — open Setup.")
        elif s.halted:
            dot_color = BROKEN_RED
            self._compact_dot.setToolTip("Halted.")
        elif s.paused:
            dot_color = DEGRADED_YELLOW
            self._compact_dot.setToolTip("Paused.")
        elif s.auto_enabled and s.armed:
            dot_color = OK_GREEN           # ENABLED — full auto live
            self._compact_dot.setToolTip("Bot ON.")
        elif s.auto_enabled:
            dot_color = DEGRADED_YELLOW    # strategy on but clicks simulated
            self._compact_dot.setToolTip("Strategy on, clicks simulated.")
        else:
            dot_color = INACTIVE_GRAY      # DISABLED — manual only
            self._compact_dot.setToolTip("Bot OFF.")
        self._compact_dot.setStyleSheet(f"color: {dot_color}; font-size: 16px;")

        # mode text — prefer the Enabled/Disabled terminology the HUD uses
        if not s.calibration_loaded:
            mode_text = "NO CAL"
        elif s.halted:
            mode_text = "HALTED"
        elif s.paused:
            mode_text = "PAUSED"
        elif s.auto_enabled and s.armed:
            mode_text = "ON"
        else:
            mode_text = "OFF"
        self._compact_mode.setText(mode_text)
        self._compact_mode.setStyleSheet(
            f"font-weight: 700; font-size: 11px; letter-spacing: 1px; color: {dot_color};"
        )

        # price
        self._compact_price.setText(f"{s.last_price:.2f}"
                                    if s.last_price is not None else "—")

        # position
        if s.position_side == "flat":
            self._compact_pos.setText("flat")
            self._compact_pos.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        else:
            side_color = OK_GREEN if s.position_side == "long" else BROKEN_RED
            self._compact_pos.setText(s.position_side.upper())
            self._compact_pos.setStyleSheet(
                f"color: {side_color}; font-size: 11px; font-weight: 700;"
            )

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

    def _on_halt(self) -> None:
        if self.controller:
            self.controller.halt("operator_halt")

    def _on_bot_toggle(self) -> None:
        """Single power toggle. ON = armed + strategy auto. OFF = disarmed
        (clicks can't reach Tradovate) + strategy silent. Price OCR runs
        in both states. Instant both ways — no confirmation popup."""
        if self.controller is None:
            return
        bot_is_on = bool(self.state.armed and self.state.auto_enabled)
        err = self.controller.turn_off() if bot_is_on else self.controller.turn_on()
        if err:
            self._show_toast(err)

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

        toggle_act = QAction("Expand HUD" if self._minimized else "Minimize HUD", menu)
        toggle_act.triggered.connect(lambda: self._set_minimized(not self._minimized))
        menu.addAction(toggle_act)

        reset = QAction("Reset position", menu)
        reset.triggered.connect(
            lambda: (self.place_default(use_saved=False), self.save_position())
        )
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
