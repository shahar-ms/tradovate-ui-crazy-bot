"""
Smoke tests for the operator UI.

These tests use pytest-qt's `qtbot` fixture; they do not boot a real
Supervisor. The UI is expected to render with default/empty state.

Mark with offscreen platform so they work on headless CI / this Windows
dev machine without popping visible windows.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from app.ui.app_signals import AppSignals, emit_event
from app.ui.controller import UiController
from app.ui.main_window import MainWindow
from app.ui.pages.calibration_page import CalibrationPage
from app.ui.pages.dashboard_page import DashboardPage
from app.ui.pages.execution_page import ExecutionPage
from app.ui.pages.logs_page import LogsPage
from app.ui.pages.run_control_page import RunControlPage
from app.ui.pages.strategy_page import StrategyPage
from app.ui.theme import STYLESHEET, status_color
from app.ui.ui_state import UiState
from app.ui.widgets.event_table import EventTable
from app.ui.widgets.status_badge import StatusBadge


def test_status_color_mapping():
    assert status_color("ok")
    assert status_color("degraded")
    assert status_color("broken")
    assert status_color("inactive")


def test_status_badge_updates(qtbot):
    b = StatusBadge("inactive")
    qtbot.addWidget(b)
    b.set_state("ok")
    assert b.property("status") == "ok"
    b.set_state("HALTED")
    assert b.property("status") == "broken"


def test_event_table_appends_and_trims(qtbot):
    t = EventTable(max_rows=3)
    qtbot.addWidget(t)
    for i in range(5):
        t.append_event({"ts_ms": 1000 + i, "level": "info",
                        "source": "test", "message": f"msg {i}"})
    assert t.rowCount() == 3  # trimmed
    # the last row should be the most recent
    assert t.item(2, 3).text() == "msg 4"


def test_ui_state_ring_buffer():
    s = UiState()
    s.RECENT_EVENTS_MAX = 3
    for i in range(5):
        s.push_event({"i": i})
    assert len(s.recent_events) == 3
    assert s.recent_events[-1]["i"] == 4


def test_main_window_builds_with_all_pages(qtbot, monkeypatch):
    # prevent QMessageBox from actually appearing (it would block the test)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning",     lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical",    lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **kw: QMessageBox.Yes)

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    window = MainWindow(signals, state, controller)
    qtbot.addWidget(window)

    from app.ui.pages.getting_started_page import GettingStartedPage
    getting = GettingStartedPage(signals, state, controller)
    dashboard = DashboardPage(signals, state, controller)
    calibration = CalibrationPage(signals)
    strategy = StrategyPage(signals)
    execution = ExecutionPage(signals)
    logs = LogsPage(signals)
    run_ctrl = RunControlPage(signals, state, controller)

    # stop every page's auto-refresh QTimer so the test doesn't depend on
    # the event loop running
    for page in (dashboard, run_ctrl, getting):
        if hasattr(page, "_refresh_timer"):
            page._refresh_timer.stop()
        if hasattr(page, "_timer"):
            page._timer.stop()
    if hasattr(logs, "_refresh_screens_timer"):
        logs._refresh_screens_timer.stop()

    window.add_page("Getting started", getting)
    window.add_page("Dashboard", dashboard)
    window.add_page("Calibration", calibration)
    window.add_page("Strategy", strategy)
    window.add_page("Execution", execution)
    window.add_page("Logs", logs)
    window.add_page("Run control", run_ctrl)

    # can switch to every page without a crash
    for i in range(window.nav.count()):
        window.go_to(i)
    assert window.stack.count() == 7

    # simulate events through the signal bus
    emit_event(signals, "info", "test", "hello from tests")
    signals.mode_changed.emit("PAPER")
    signals.armed_changed.emit(False)
    signals.health_updated.emit({"health_state": "ok"})
    signals.anchor_guard_changed.emit(True, 0.95)
    signals.halt_triggered.emit("unit_test_halt")
    assert state is not None


def test_dashboard_reflects_ui_state(qtbot):
    signals = AppSignals()
    state = UiState()
    state.last_price = 19234.25
    state.last_confidence = 92.5
    state.price_stream_health = "ok"
    state.position_side = "long"
    state.entry_price = 19200.0
    state.stop_price = 19180.0
    state.target_price = 19260.0
    state.session_id = "sess_abc"
    state.calibration_loaded = True
    state.monitor_index = 1
    state.screen_size = (1920, 1080)

    controller = UiController(signals=signals, state=state)
    page = DashboardPage(signals, state, controller)
    page._refresh_timer.stop()
    qtbot.addWidget(page)
    # trigger a refresh
    page._refresh_values()

    # basic sanity on a couple of value labels
    assert page.lv_price._value.text() == "19234.25"
    assert page.lv_position._value.text() == "long"
    assert page.lv_calibrated._value.text() == "yes"


def test_logs_page_appends_events(qtbot):
    signals = AppSignals()
    page = LogsPage(signals)
    qtbot.addWidget(page)
    # stop the screenshots refresh timer so it can't fire during the test
    page._refresh_screens_timer.stop()

    emit_event(signals, "warn", "test-source", "something happened")
    signals.price_updated.emit({"ts_ms": 1000, "price": 19200.0, "confidence": 90.0,
                                "accepted": True, "reject_reason": None, "frame_id": 1})
    signals.execution_ack.emit({"ts_ms": 2000, "action": "BUY", "status": "ok",
                                "message": "dry_run"})
    signals.halt_triggered.emit("unit_halt")

    assert page.events.rowCount() >= 1
    assert page.price_events.rowCount() >= 1
    assert page.exec_events.rowCount() >= 1
    assert page.halt_events.rowCount() >= 1


def test_getting_started_steps_follow_user_progress(qtbot, monkeypatch):
    """Verify the wizard tracks the three guided milestones correctly."""
    from app.ui.pages.getting_started_page import GettingStartedPage

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    page = GettingStartedPage(signals, state, controller)
    page._timer.stop()
    qtbot.addWidget(page)

    # scenario A: nothing done -> step 1 pending + active, 2 and 3 pending+inactive
    # force screen_map "missing" to avoid relying on real filesystem state
    from app.utils import paths
    import pathlib

    class FakePath:
        def exists(self):
            return False

    monkeypatch.setattr(paths, "screen_map_path", lambda: FakePath())
    page._refresh()
    assert page._active_step == 1
    assert not page.step2.action_btn.isEnabled()
    assert not page.step3.action_btn.isEnabled()
    assert page.step1.action_btn.isEnabled()

    # scenario B: calibration "done" via mocked validator
    class TruePath:
        def exists(self):
            return True

    monkeypatch.setattr(paths, "screen_map_path", lambda: TruePath())

    class FakeReport:
        ready = True
        lines = ["[OK] all good", "READY_FOR_FILE_02 = true"]

    from app.ui.pages import getting_started_page as gsp_mod
    monkeypatch.setattr(gsp_mod, "validate_calibration", lambda offline=False: FakeReport())

    # still need to flow ticks for step 2
    state.mode = "PRICE_DEBUG"
    state.price_stream_health = "ok"
    state.accepted_tick_count = 3  # below threshold
    page._refresh()
    assert page._active_step == 2
    assert page.step2.action_btn.isEnabled()
    assert not page.step3.action_btn.isEnabled()

    # scenario C: enough ticks -> step 2 done, step 3 becomes active
    state.accepted_tick_count = 15
    state.mode = "PAPER"
    state.signals_emitted_count = 0
    page._refresh()
    assert page._active_step == 3
    assert page.step3.action_btn.isEnabled()

    # scenario D: a signal fires -> step 3 done, all three green
    state.signals_emitted_count = 1
    page._refresh()
    s3 = page._step3_status()
    assert s3.is_done


def test_getting_started_step_status_helpers(qtbot, monkeypatch, tmp_path):
    from app.ui.pages.getting_started_page import GettingStartedPage, MIN_TICKS_TO_PASS

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    page = GettingStartedPage(signals, state, controller)
    page._timer.stop()
    qtbot.addWidget(page)

    # step 2 not-started when DISCONNECTED
    state.mode = "DISCONNECTED"
    s2 = page._step2_status()
    assert not s2.is_done
    assert "not running" in s2.headline

    # step 2 active but not done when ticks below threshold
    state.mode = "PRICE_DEBUG"
    state.accepted_tick_count = MIN_TICKS_TO_PASS - 1
    state.price_stream_health = "ok"
    s2b = page._step2_status()
    assert not s2b.is_done
    assert s2b.state == "active"

    # step 2 done when threshold hit + health ok
    state.accepted_tick_count = MIN_TICKS_TO_PASS
    s2c = page._step2_status()
    assert s2c.is_done


def test_floating_hud_reflects_state(qtbot):
    from app.ui.widgets.floating_hud import FloatingHud

    signals = AppSignals()
    state = UiState()
    state.mode = "PAPER"
    state.last_price = 19234.25
    state.last_confidence = 91.0
    state.price_stream_health = "ok"
    state.position_side = "long"
    state.last_intent_action = "BUY"
    state.last_ack_status = "ok"

    hud = FloatingHud(signals, state)
    hud._refresh_timer.stop()
    qtbot.addWidget(hud)
    hud._refresh_from_state()

    assert hud._mode_lbl.text() == "PAPER"
    assert hud._price_lbl.text() == "19234.25"
    assert "ok" in hud._health_lbl.text()
    assert "long" in hud._pos_lbl.text()
    assert "BUY" in hud._intent_lbl.text()
    assert "ok" in hud._ack_lbl.text()
    # use isHidden() because isVisible() also reports False for unshown parents
    assert hud._halt_lbl.isHidden()


def test_floating_hud_halted_shows_banner(qtbot):
    from app.ui.widgets.floating_hud import FloatingHud

    signals = AppSignals()
    state = UiState()
    state.halted = True
    state.halt_reason = "anchor_drift"
    hud = FloatingHud(signals, state)
    hud._refresh_timer.stop()
    qtbot.addWidget(hud)
    hud._refresh_from_state()

    assert not hud._halt_lbl.isHidden()
    assert "anchor_drift" in hud._halt_lbl.text()


def test_floating_hud_default_position_left_middle(qtbot):
    from app.ui.widgets.floating_hud import FloatingHud, HUD_HEIGHT, HUD_LEFT_MARGIN
    from PySide6.QtGui import QGuiApplication

    signals = AppSignals()
    state = UiState()
    hud = FloatingHud(signals, state)
    hud._refresh_timer.stop()
    qtbot.addWidget(hud)
    hud.place_default()

    screen = QGuiApplication.primaryScreen()
    geom = screen.availableGeometry()
    expected_x = geom.left() + HUD_LEFT_MARGIN
    assert hud.x() == expected_x
    # y must be below the midpoint of the screen (middle-low)
    assert hud.y() > geom.top() + geom.height() // 2 - HUD_HEIGHT


def test_main_window_toggle_hud_creates_and_hides(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)

    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    window = MainWindow(signals, state, controller)
    qtbot.addWidget(window)

    # no HUD initially
    assert window._hud is None
    window.toggle_hud()
    assert window._hud is not None
    assert window._hud.isVisible()
    qtbot.addWidget(window._hud)
    window._hud._refresh_timer.stop()

    window.toggle_hud()
    assert not window._hud.isVisible()


def test_calibration_clear_uses_items_list_selection(qtbot, monkeypatch):
    """Clearing should respect the row selected in the 'Marked items' list."""
    from PySide6.QtWidgets import QMessageBox
    import numpy as np

    from app.models.common import Point, Region
    from app.ui.pages.calibration_page import CalibTargets, CalibrationPage

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    # pretend an image is loaded so marking is allowed
    page._full_image = np.zeros((100, 200, 3), dtype=np.uint8)
    page._image_source = "capture"
    page._monitor_size = (200, 100)
    page._refresh_image_buttons()

    # seed three marks
    page.targets = CalibTargets(
        buy=Point(x=10, y=20),
        sell=Point(x=30, y=40),
        cancel=Point(x=50, y=60),
    )
    page._refresh_items_list()

    # select the 'sell' row in the items list (index 3 in ITEMS order)
    sell_row = -1
    for i in range(page.items_list.count()):
        if page.items_list.item(i).data(Qt.UserRole) == "sell":
            sell_row = i
            break
    assert sell_row >= 0
    page.items_list.setCurrentRow(sell_row)

    # combo is pointing at 'anchor' (first), but list selection must win
    page.item_combo.setCurrentIndex(page.item_combo.findData("anchor"))

    page._clear_current_item()

    assert page.targets.sell is None
    assert page.targets.buy is not None
    assert page.targets.cancel is not None


def test_calibration_clear_falls_back_to_combo(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import numpy as np

    from app.models.common import Point
    from app.ui.pages.calibration_page import CalibTargets, CalibrationPage

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    page._full_image = np.zeros((100, 200, 3), dtype=np.uint8)
    page._refresh_image_buttons()
    page.targets = CalibTargets(buy=Point(x=10, y=20))
    page._refresh_items_list()

    # no list selection
    page.items_list.setCurrentRow(-1)
    # combo on 'buy'
    page.item_combo.setCurrentIndex(page.item_combo.findData("buy"))
    page._clear_current_item()
    assert page.targets.buy is None


def test_calibration_countdown_updates_label_and_fires_callback(qtbot, monkeypatch):
    """Start a countdown, manually tick it, confirm the callback fires."""
    from app.ui.pages.calibration_page import CalibrationPage

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    fired = {"count": 0}
    page._start_countdown(on_zero=lambda: fired.__setitem__("count", fired["count"] + 1),
                          label_prefix="Capturing in", seconds=3)
    # label is set to initial seconds value
    assert "3" in page.btn_capture.text()
    # simulate three ticks
    page._countdown_tick()
    assert "2" in page.btn_capture.text()
    page._countdown_tick()
    assert "1" in page.btn_capture.text()
    page._countdown_tick()
    # callback must have fired exactly once
    assert fired["count"] == 1
    # and the button label is restored
    assert page.btn_capture.text() == page._countdown_default_label


def test_calibration_countdown_disables_other_buttons(qtbot):
    from app.ui.pages.calibration_page import CalibrationPage

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    page._start_countdown(on_zero=lambda: None, label_prefix="Capturing in", seconds=2)
    assert not page.btn_capture.isEnabled()
    assert not page.btn_capture_window.isEnabled()
    assert not page.btn_load_file.isEnabled()

    # drain the countdown
    page._countdown_tick()
    page._countdown_tick()
    # buttons restored (no image loaded -> capture/load enabled, reset disabled)
    assert page.btn_capture.isEnabled()
    assert page.btn_capture_window.isEnabled()
    assert page.btn_load_file.isEnabled()
    assert not page.btn_reset_image.isEnabled()


def test_calibration_countdown_ignored_while_already_running(qtbot):
    from app.ui.pages.calibration_page import CalibrationPage

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    calls = []
    page._start_countdown(on_zero=lambda: calls.append("first"), seconds=3)
    # second call while first is running must be a no-op
    page._start_countdown(on_zero=lambda: calls.append("second"), seconds=3)
    page._countdown_tick()
    page._countdown_tick()
    page._countdown_tick()
    assert calls == ["first"]


def test_calibration_image_buttons_toggle(qtbot):
    import numpy as np
    from app.ui.pages.calibration_page import CalibrationPage

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    # initially: capture+load enabled, reset disabled
    assert page.btn_capture.isEnabled()
    assert page.btn_load_file.isEnabled()
    assert not page.btn_reset_image.isEnabled()

    # simulate loading an image
    page._set_image(np.zeros((50, 80, 3), dtype=np.uint8),
                    source="file:fake.png", monitor_index=1, size=(80, 50))
    assert not page.btn_capture.isEnabled()
    assert not page.btn_load_file.isEnabled()
    assert page.btn_reset_image.isEnabled()


def test_calibration_load_from_file_reads_png(qtbot, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from app.ui.pages.calibration_page import CalibrationPage

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)

    # write a fake screenshot
    img = np.zeros((60, 120, 3), dtype=np.uint8)
    img[20:40, 40:80] = (0, 200, 0)
    png = tmp_path / "shot.png"
    cv2.imwrite(str(png), img)

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **kw: (str(png), "Images"))

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    page._load_from_file()

    assert page._full_image is not None
    assert page._monitor_size == (120, 60)
    assert page._image_source.startswith("file:")
    # buttons have flipped
    assert page.btn_reset_image.isEnabled()
    assert not page.btn_capture.isEnabled()


def test_run_control_page_buttons_disabled_when_not_running(qtbot):
    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    page = RunControlPage(signals, state, controller)
    page._timer.stop()
    qtbot.addWidget(page)
    page._refresh()

    # controller is not running — stop/halt/cancel should be disabled
    assert not page.btn_halt.isEnabled()
    assert not page.btn_cancel.isEnabled()
    assert not page.btn_shutdown.isEnabled()
    # price/paper start should be enabled because no supervisor yet
    assert page.btn_price.isEnabled()
    assert page.btn_paper.isEnabled()
    # arm should be disabled (calibration not loaded in this UiState)
    assert not page.btn_arm.isEnabled()
