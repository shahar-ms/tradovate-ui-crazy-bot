"""
Entry point for the operator UI.

    python -m app.ui.run_ui

The UI starts disconnected. The user picks a mode from the Dashboard or Run
Control page to boot the Supervisor.
"""

from __future__ import annotations

import logging
import os
import sys

# On Windows, tell Qt to prefer the software OpenGL path if hardware fails —
# harmless if hardware works.
os.environ.setdefault("QT_OPENGL", "desktop")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.app_signals import AppSignals  # noqa: E402
from app.ui.controller import UiController  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.pages.calibration_page import CalibrationPage  # noqa: E402
from app.ui.pages.dashboard_page import DashboardPage  # noqa: E402
from app.ui.pages.execution_page import ExecutionPage  # noqa: E402
from app.ui.pages.logs_page import LogsPage  # noqa: E402
from app.ui.pages.run_control_page import RunControlPage  # noqa: E402
from app.ui.pages.strategy_page import StrategyPage  # noqa: E402
from app.ui.theme import STYLESHEET  # noqa: E402
from app.ui.ui_state import UiState  # noqa: E402
from app.utils.logging_utils import setup_logging  # noqa: E402

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    setup_logging()

    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, False)
    app = QApplication(argv)
    app.setApplicationName("Tradovate UI bot")
    app.setStyleSheet(STYLESHEET)

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)

    window = MainWindow(signals, state, controller)

    # build pages in nav order
    dashboard = DashboardPage(signals, state, controller)
    calibration = CalibrationPage(signals)
    strategy = StrategyPage(signals)
    execution = ExecutionPage(signals)
    logs = LogsPage(signals)
    run_ctrl = RunControlPage(signals, state, controller)

    window.add_page("Dashboard",   dashboard)
    window.add_page("Calibration", calibration)
    window.add_page("Strategy",    strategy)
    window.add_page("Execution",   execution)
    window.add_page("Logs",        logs)
    window.add_page("Run control", run_ctrl)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
