"""
Tiny app shell. No QMainWindow, no navigation, no tray icon.
One floating always-on-top HUD is the entire app.

Boot sequence:
  1. setup logging
  2. apply the theme stylesheet
  3. if calibration is invalid → show CalibrationDialog modally first.
     Cancel → exit cleanly.
  4. instantiate AppSignals / UiState / UiController / FloatingHud
  5. start the bot in PRICE_DEBUG mode (disarmed) automatically
  6. wire Setup button + calibration-reloaded → executor reload
  7. show the HUD
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from app.calibration.validator import validate_calibration
from app.models.config import load_screen_map
from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.dialogs.calibration_dialog import CalibrationDialog
from app.ui.theme import STYLESHEET
from app.ui.ui_state import UiState
from app.ui.widgets.floating_hud import FloatingHud
from app.utils import paths
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


def _calibration_valid() -> bool:
    try:
        return validate_calibration(offline=True).ready
    except Exception:
        return False


def boot(argv: Optional[list[str]] = None) -> int:
    setup_logging()
    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, False)
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Tradovate bot")
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(True)

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)

    # Step 1: if calibration missing/invalid, force the user to calibrate before the HUD appears.
    if not _calibration_valid():
        log.info("no valid calibration yet — opening CalibrationDialog")
        dlg = CalibrationDialog(signals)
        dlg.exec()
        if not _calibration_valid():
            log.warning("calibration still invalid after dialog — exiting cleanly")
            return 0

    # Sync UiState so the HUD's arm-enable rules work on first render
    try:
        sm = load_screen_map(paths.screen_map_path())
        state.calibration_loaded = True
        state.monitor_index = sm.monitor_index
        state.screen_size = (sm.screen_width, sm.screen_height)
    except Exception as e:
        log.warning("failed to load screen_map post-calibration: %s", e)

    # Step 2: build the HUD. Must exist before we start the supervisor so
    # its signals bridge to the UI.
    hud = FloatingHud(signals=signals, state=state, controller=controller)
    hud.place_default()
    hud.show()

    # Setup button → open calibration dialog, then reload executor after save
    def _on_setup():
        dlg = CalibrationDialog(signals, hud)
        dlg.exec()
        # always reload if calibration file is current and valid
        if _calibration_valid():
            try:
                new_map = load_screen_map(paths.screen_map_path())
                controller.reload_executor_screen_map(new_map)
                state.calibration_loaded = True
                state.monitor_index = new_map.monitor_index
                state.screen_size = (new_map.screen_width, new_map.screen_height)
            except Exception as e:
                log.exception("post-Setup reload failed: %s", e)
                QMessageBox.warning(hud, "Reload failed",
                                    f"Calibration saved but reload failed:\n{e}")

    hud.setup_requested.connect(_on_setup)

    # Step 3: auto-start the bot in PRICE_DEBUG mode
    err = controller.start(mode="PRICE_DEBUG", armed=False)
    if err:
        QMessageBox.warning(hud, "Startup warning",
                            f"Bot didn't auto-start:\n{err}\n\nUse Setup to fix calibration.")

    # Step 4: ensure controller stops when the HUD closes
    def _on_about_to_quit():
        try:
            controller.stop()
        except Exception:
            log.exception("controller.stop raised on quit")

    app.aboutToQuit.connect(_on_about_to_quit)

    return app.exec()
