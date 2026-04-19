"""
Dashboard: at-a-glance answers to 'is the bot safe, what's happening, what did
it want to do?'. Reads from UiState on a light QTimer + reactive signals.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.ui_state import UiState
from app.ui.widgets.event_table import EventTable
from app.ui.widgets.labeled_value import LabeledValue
from app.ui.widgets.panel import Panel


class DashboardPage(QWidget):
    def __init__(self, signals: AppSignals, state: UiState, controller: UiController,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.state = state
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        # top row: quick-start buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_price = QPushButton("Start Price Debug")
        self.btn_paper = QPushButton("Start Paper Mode")
        self.btn_arm = QPushButton("Arm Live Trading")
        self.btn_stop = QPushButton("Stop Bot")
        self.btn_hud = QPushButton("Show Floating HUD")
        self.btn_hud.setToolTip(
            "Open a small always-on-top HUD with mode, price, position and last ack.\n"
            "Park it over an empty spot of the Tradovate screen.\n"
            "Shortcut: Ctrl+Shift+H"
        )
        for b in (self.btn_price, self.btn_paper, self.btn_arm, self.btn_stop, self.btn_hud):
            b.setMinimumHeight(34)
            btn_row.addWidget(b)
        self.btn_price.setProperty("role", "primary")
        self.btn_paper.setProperty("role", "primary")
        self.btn_arm.setProperty("role", "arm")
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # grid of cards
        grid = QGridLayout()
        grid.setSpacing(10)

        self.runtime_panel = self._build_runtime_panel()
        self.market_panel = self._build_market_panel()
        self.strategy_panel = self._build_strategy_panel()
        self.execution_panel = self._build_execution_panel()
        self.guard_panel = self._build_guard_panel()

        grid.addWidget(self.runtime_panel, 0, 0)
        grid.addWidget(self.market_panel, 0, 1)
        grid.addWidget(self.strategy_panel, 0, 2)
        grid.addWidget(self.execution_panel, 1, 0)
        grid.addWidget(self.guard_panel, 1, 1)

        # recent events
        events_panel = Panel("Recent events")
        self.events = EventTable(max_rows=60, compact=True)
        events_panel.add(self.events)
        grid.addWidget(events_panel, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        root.addLayout(grid, 1)

        # wiring
        self.btn_price.clicked.connect(lambda: self._start_mode("PRICE_DEBUG"))
        self.btn_paper.clicked.connect(lambda: self._start_mode("PAPER"))
        self.btn_arm.clicked.connect(self._try_arm)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_hud.clicked.connect(self._toggle_hud)

        self.signals.event_logged.connect(self._on_event_logged)
        self.signals.mode_changed.connect(lambda _m: self._refresh_buttons())
        self.signals.armed_changed.connect(lambda _a: self._refresh_buttons())
        self.signals.halt_triggered.connect(lambda _r: self._refresh_buttons())

        # poll timer to refresh value labels from UiState
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(400)
        self._refresh_timer.timeout.connect(self._refresh_values)
        self._refresh_timer.start()

        self._refresh_values()
        self._refresh_buttons()

    # ---- panel builders ---- #

    def _build_runtime_panel(self) -> Panel:
        p = Panel("Runtime")
        self.lv_mode = LabeledValue("Mode", value_big=True)
        self.lv_armed = LabeledValue("Armed")
        self.lv_halted = LabeledValue("Halted")
        self.lv_halt_reason = LabeledValue("Halt reason")
        self.lv_session = LabeledValue("Session")
        self.lv_uptime = LabeledValue("Uptime")
        for w in (self.lv_mode, self.lv_armed, self.lv_halted,
                  self.lv_halt_reason, self.lv_session, self.lv_uptime):
            p.add(w)
        p.add_stretch()
        return p

    def _build_market_panel(self) -> Panel:
        p = Panel("Market")
        self.lv_price = LabeledValue("Last price", value_big=True)
        self.lv_health = LabeledValue("Stream health")
        self.lv_conf = LabeledValue("OCR confidence")
        self.lv_ticks = LabeledValue("Accepted / rejected")
        self.lv_price_age = LabeledValue("Last tick age")
        self.lv_reject = LabeledValue("Last reject")
        for w in (self.lv_price, self.lv_health, self.lv_conf,
                  self.lv_ticks, self.lv_price_age, self.lv_reject):
            p.add(w)
        p.add_stretch()
        return p

    def _build_strategy_panel(self) -> Panel:
        p = Panel("Strategy")
        self.lv_position = LabeledValue("Position", value_big=True)
        self.lv_entry = LabeledValue("Entry")
        self.lv_stop = LabeledValue("Stop")
        self.lv_target = LabeledValue("Target")
        self.lv_last_intent = LabeledValue("Last intent")
        for w in (self.lv_position, self.lv_entry, self.lv_stop,
                  self.lv_target, self.lv_last_intent):
            p.add(w)
        p.add_stretch()
        return p

    def _build_execution_panel(self) -> Panel:
        p = Panel("Execution")
        self.lv_last_ack = LabeledValue("Last ack")
        self.lv_ack_age = LabeledValue("Last ack age")
        self.lv_unknown_streak = LabeledValue("Unknown ack streak")
        for w in (self.lv_last_ack, self.lv_ack_age, self.lv_unknown_streak):
            p.add(w)
        p.add_stretch()
        return p

    def _build_guard_panel(self) -> Panel:
        p = Panel("Anchor / Screen")
        self.lv_anchor = LabeledValue("Anchor guard")
        self.lv_anchor_sim = LabeledValue("Anchor similarity")
        self.lv_monitor = LabeledValue("Monitor")
        self.lv_resolution = LabeledValue("Resolution")
        self.lv_calibrated = LabeledValue("Calibration loaded")
        for w in (self.lv_anchor, self.lv_anchor_sim, self.lv_monitor,
                  self.lv_resolution, self.lv_calibrated):
            p.add(w)
        p.add_stretch()
        return p

    # ---- refresh ---- #

    def _refresh_values(self) -> None:
        s = self.state
        self.lv_mode.set_value(s.mode,
                               status="ok" if s.mode in ("PAPER", "PRICE_DEBUG", "ARMED")
                               else "broken" if s.mode == "HALTED" else "inactive")
        self.lv_armed.set_value("YES" if s.armed else "no",
                                status="degraded" if s.armed else "inactive")
        self.lv_halted.set_value("YES" if s.halted else "no",
                                 status="broken" if s.halted else "ok")
        self.lv_halt_reason.set_value(s.halt_reason or "-",
                                      status="broken" if s.halted else "inactive")
        self.lv_session.set_value(s.session_id or "-")
        self.lv_uptime.set_value(self._fmt_uptime(s.uptime_seconds))

        # market
        self.lv_price.set_value(f"{s.last_price:.2f}" if s.last_price is not None else "—")
        self.lv_health.set_value(s.price_stream_health, status=s.price_stream_health)
        self.lv_conf.set_value(f"{s.last_confidence:.1f}" if s.last_confidence else "—")
        self.lv_ticks.set_value(f"{s.accepted_tick_count} / {s.rejected_tick_count}")
        if s.last_price_ts_ms:
            age_ms = int(time.time() * 1000) - s.last_price_ts_ms
            self.lv_price_age.set_value(f"{age_ms} ms",
                                        status="ok" if age_ms < 2000 else "degraded")
        else:
            self.lv_price_age.set_value("—")
        self.lv_reject.set_value(s.last_reject_reason or "-")

        # strategy
        self.lv_position.set_value(s.position_side,
                                   status="degraded" if s.position_side != "flat" else "inactive")
        self.lv_entry.set_value(f"{s.entry_price:.2f}" if s.entry_price is not None else "—")
        self.lv_stop.set_value(f"{s.stop_price:.2f}" if s.stop_price is not None else "—")
        self.lv_target.set_value(f"{s.target_price:.2f}" if s.target_price is not None else "—")
        intent = s.last_intent_action or "-"
        if s.last_intent_reason:
            intent = f"{intent} ({s.last_intent_reason})"
        self.lv_last_intent.set_value(intent)

        # execution
        ack = s.last_ack_status or "-"
        self.lv_last_ack.set_value(ack,
                                   status="ok" if ack == "ok"
                                   else "broken" if ack in ("failed", "blocked")
                                   else "degraded" if ack == "unknown" else "inactive")
        if s.last_ack_ts_ms:
            age_ms = int(time.time() * 1000) - s.last_ack_ts_ms
            self.lv_ack_age.set_value(f"{age_ms} ms")
        else:
            self.lv_ack_age.set_value("—")
        self.lv_unknown_streak.set_value(
            str(s.consecutive_unknown_acks),
            status="broken" if s.consecutive_unknown_acks >= 2
            else "degraded" if s.consecutive_unknown_acks else "ok",
        )

        # guard
        self.lv_anchor.set_value("ok" if s.anchor_ok else "drift",
                                 status="ok" if s.anchor_ok else "broken")
        self.lv_anchor_sim.set_value(f"{s.anchor_similarity:.3f}" if s.anchor_similarity else "—")
        self.lv_monitor.set_value(str(s.monitor_index))
        self.lv_resolution.set_value(f"{s.screen_size[0]}x{s.screen_size[1]}"
                                     if s.screen_size[0] else "—")
        self.lv_calibrated.set_value("yes" if s.calibration_loaded else "no",
                                     status="ok" if s.calibration_loaded else "broken")

    def _refresh_buttons(self) -> None:
        running = self.controller.is_running()
        halted = self.state.halted
        self.btn_stop.setEnabled(running)
        # Start buttons: enabled when not running
        self.btn_price.setEnabled(not running)
        self.btn_paper.setEnabled(not running)
        # Arm: only from PAPER, not halted
        arm_checks = self.controller.pre_arm_checks()
        self.btn_arm.setEnabled(all(c.ok for c in arm_checks) and not halted)

    @Slot(dict)
    def _on_event_logged(self, event: dict) -> None:
        self.events.append_event(event)
        self.state.push_event(event)

    # ---- actions ---- #

    def _start_mode(self, mode: str) -> None:
        err = self.controller.start(mode=mode, armed=False)
        if err:
            self._handle_start_failure(mode, err)
        self._refresh_buttons()

    def _handle_start_failure(self, mode: str, err: str) -> None:
        """Offer Re-calibrate / Start anyway when bootstrap rejects the start."""
        from app.ui.dialogs.calibration_failed_dialog import CalibrationFailedDialog
        lines = list(self.controller.last_start_report_lines)
        if not lines:
            QMessageBox.critical(self, "Start failed", err)
            return
        dlg = CalibrationFailedDialog(
            message=self.controller.last_start_error or err,
            report_lines=lines,
            parent=self,
        )
        if not dlg.exec():
            return
        if dlg.choice == CalibrationFailedDialog.RECALIBRATE:
            w = self.window()
            if hasattr(w, "_calibration_index") and hasattr(w, "go_to"):
                w.go_to(w._calibration_index)  # type: ignore[attr-defined]
            return
        if dlg.choice == CalibrationFailedDialog.START_ANYWAY:
            err2 = self.controller.start(mode=mode, armed=False,
                                         skip_calibration_check=True)
            if err2:
                QMessageBox.critical(self, "Start failed", err2)

    def _try_arm(self) -> None:
        from app.ui.dialogs.arm_confirm_dialog import ArmConfirmDialog
        dlg = ArmConfirmDialog(self.controller, self.state, self)
        if dlg.exec():
            err = self.controller.arm()
            if err:
                QMessageBox.critical(self, "Arm failed", err)
        self._refresh_buttons()

    def _stop(self) -> None:
        self.controller.stop()
        self._refresh_buttons()

    def _toggle_hud(self) -> None:
        # Walk up the parent chain to find the MainWindow.
        w = self.window()
        if hasattr(w, "toggle_hud"):
            w.toggle_hud()

    # ---- helpers ---- #

    @staticmethod
    def _fmt_uptime(seconds: int) -> str:
        if seconds <= 0:
            return "—"
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m}m {s:02d}s"
