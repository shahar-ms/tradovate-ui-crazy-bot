"""
Run control: the explicit operational page where the operator deliberately
chooses the mode. All safety gates are explained inline.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.dialogs.arm_confirm_dialog import ArmConfirmDialog
from app.ui.dialogs.halt_reason_dialog import HaltReasonDialog
from app.ui.theme import BROKEN_RED, OK_GREEN
from app.ui.ui_state import UiState
from app.ui.widgets.labeled_value import LabeledValue
from app.ui.widgets.panel import Panel


class RunControlPage(QWidget):
    def __init__(self, signals: AppSignals, state: UiState, controller: UiController,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.state = state
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        # state panel
        state_panel = Panel("Current state")
        self.lv_mode = LabeledValue("Mode", value_big=True)
        self.lv_armed = LabeledValue("Armed")
        self.lv_halted = LabeledValue("Halted")
        self.lv_health = LabeledValue("Price health")
        self.lv_anchor = LabeledValue("Anchor guard")
        for w in (self.lv_mode, self.lv_armed, self.lv_halted, self.lv_health, self.lv_anchor):
            state_panel.add(w)
        root.addWidget(state_panel)

        # controls panel
        ctrl_panel = Panel("Controls")
        grid = QGridLayout()
        grid.setSpacing(8)

        self.btn_start_calib = QPushButton("Open Calibrator (external)")
        self.btn_price = QPushButton("Start Price Debug")
        self.btn_paper = QPushButton("Start Paper Mode")
        self.btn_arm = QPushButton("Arm Live Trading")
        self.btn_disarm = QPushButton("Disarm")
        self.btn_cancel = QPushButton("Cancel All")
        self.btn_halt = QPushButton("Halt Now")
        self.btn_resume = QPushButton("Reset Halt")
        self.btn_shutdown = QPushButton("Shutdown Bot")

        self.btn_arm.setProperty("role", "arm")
        self.btn_halt.setProperty("role", "halt")
        self.btn_cancel.setProperty("role", "cancel")
        self.btn_shutdown.setProperty("role", "danger")
        self.btn_price.setProperty("role", "primary")
        self.btn_paper.setProperty("role", "primary")

        for b in (self.btn_start_calib, self.btn_price, self.btn_paper, self.btn_arm,
                  self.btn_disarm, self.btn_cancel, self.btn_halt, self.btn_resume,
                  self.btn_shutdown):
            b.setMinimumHeight(38)

        grid.addWidget(self.btn_start_calib, 0, 0)
        grid.addWidget(self.btn_price,       0, 1)
        grid.addWidget(self.btn_paper,       0, 2)
        grid.addWidget(self.btn_arm,         1, 0)
        grid.addWidget(self.btn_disarm,      1, 1)
        grid.addWidget(self.btn_cancel,      1, 2)
        grid.addWidget(self.btn_halt,        2, 0)
        grid.addWidget(self.btn_resume,      2, 1)
        grid.addWidget(self.btn_shutdown,    2, 2)

        ctrl_panel.add(self._wrap_grid(grid))
        root.addWidget(ctrl_panel)

        # gate-reasons panel
        self.gates_panel = Panel("Arm readiness")
        self.gates_html = QMessageBox()  # reuse text rendering
        from PySide6.QtWidgets import QLabel as _QL
        self.gates_label = _QL()
        self.gates_label.setTextFormat(Qt.RichText)
        self.gates_label.setWordWrap(True)
        self.gates_panel.add(self.gates_label)
        root.addWidget(self.gates_panel)
        root.addStretch(1)

        # wiring
        self.btn_start_calib.clicked.connect(self._open_calibrator)
        self.btn_price.clicked.connect(lambda: self._start_mode("PRICE_DEBUG"))
        self.btn_paper.clicked.connect(lambda: self._start_mode("PAPER"))
        self.btn_arm.clicked.connect(self._try_arm)
        self.btn_disarm.clicked.connect(controller.disarm)
        self.btn_cancel.clicked.connect(controller.cancel_all)
        self.btn_halt.clicked.connect(self._halt)
        self.btn_resume.clicked.connect(self._resume)
        self.btn_shutdown.clicked.connect(self._shutdown)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self._refresh()

    def _wrap_grid(self, grid: QGridLayout) -> QWidget:
        w = QWidget()
        w.setLayout(grid)
        return w

    # ---- actions ---- #

    def _open_calibrator(self) -> None:
        """Run the existing OpenCV calibrator as a subprocess (blocks until user closes)."""
        import subprocess
        import sys
        from app.utils import paths
        project_root = paths.project_root()
        try:
            subprocess.Popen([sys.executable, "-m", "app.calibration.calibrator"],
                             cwd=str(project_root))
            QMessageBox.information(self, "Calibrator",
                                    "Calibrator launched in a separate window.")
        except Exception as e:
            QMessageBox.critical(self, "Calibrator", f"Failed to launch: {e}")

    def _start_mode(self, mode: str) -> None:
        err = self.controller.start(mode=mode, armed=False)
        if err:
            self._handle_start_failure(mode, err)
        self._refresh()

    def _handle_start_failure(self, mode: str, err: str) -> None:
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
        dlg = ArmConfirmDialog(self.controller, self.state, self)
        if dlg.exec():
            err = self.controller.arm()
            if err:
                QMessageBox.critical(self, "Arm blocked", err)
        self._refresh()

    def _halt(self) -> None:
        if QMessageBox.question(self, "Halt?",
                                "Halt the bot? No new entries will be sent.",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.controller.halt("operator_halt")

    def _resume(self) -> None:
        if not self.state.halted:
            QMessageBox.information(self, "Not halted", "Bot is not currently halted.")
            return
        dlg = HaltReasonDialog(self.state.halt_reason or "-", self)
        if dlg.exec():
            # restart in PAPER mode to clear halted state
            self.controller.stop()
            err = self.controller.start(mode="PAPER", armed=False)
            if err:
                QMessageBox.critical(self, "Resume failed", err)

    def _shutdown(self) -> None:
        if QMessageBox.question(self, "Shutdown bot?",
                                "Stop the bot (supervisor + threads)?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.controller.stop()

    # ---- refresh ---- #

    def _refresh(self) -> None:
        s = self.state
        self.lv_mode.set_value(s.mode,
                               status="ok" if s.mode in ("PRICE_DEBUG", "PAPER", "ARMED")
                               else "broken" if s.mode == "HALTED" else "inactive")
        self.lv_armed.set_value("YES" if s.armed else "no",
                                status="degraded" if s.armed else "inactive")
        self.lv_halted.set_value("YES" if s.halted else "no",
                                 status="broken" if s.halted else "ok")
        self.lv_health.set_value(s.price_stream_health, status=s.price_stream_health)
        self.lv_anchor.set_value("ok" if s.anchor_ok else "drift",
                                 status="ok" if s.anchor_ok else "broken")

        # buttons
        running = self.controller.is_running()
        self.btn_price.setEnabled(not running)
        self.btn_paper.setEnabled(not running)
        self.btn_disarm.setEnabled(s.armed)
        self.btn_cancel.setEnabled(running)
        self.btn_halt.setEnabled(running and not s.halted)
        self.btn_resume.setEnabled(s.halted)
        self.btn_shutdown.setEnabled(running)

        arm_checks = self.controller.pre_arm_checks()
        self.btn_arm.setEnabled(all(c.ok for c in arm_checks))

        # arm readiness rendering
        lines = []
        for c in arm_checks:
            color = OK_GREEN if c.ok else BROKEN_RED
            icon = "✓" if c.ok else "✗"
            status = "ok" if c.ok else c.reason
            lines.append(f"<span style='color:{color}; font-weight:700;'>{icon}</span> "
                         f"{c.name} — {status}")
        self.gates_label.setText("<br>".join(lines))
