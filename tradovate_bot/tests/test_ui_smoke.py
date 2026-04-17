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

    dashboard = DashboardPage(signals, state, controller)
    calibration = CalibrationPage(signals)
    strategy = StrategyPage(signals)
    execution = ExecutionPage(signals)
    logs = LogsPage(signals)
    run_ctrl = RunControlPage(signals, state, controller)

    # stop every page's auto-refresh QTimer so the test doesn't depend on
    # the event loop running
    for page in (dashboard, run_ctrl):
        if hasattr(page, "_refresh_timer"):
            page._refresh_timer.stop()
        if hasattr(page, "_timer"):
            page._timer.stop()
    if hasattr(logs, "_refresh_screens_timer"):
        logs._refresh_screens_timer.stop()

    window.add_page("Dashboard", dashboard)
    window.add_page("Calibration", calibration)
    window.add_page("Strategy", strategy)
    window.add_page("Execution", execution)
    window.add_page("Logs", logs)
    window.add_page("Run control", run_ctrl)

    # can switch to every page without a crash
    for i in range(window.nav.count()):
        window.go_to(i)
    assert window.stack.count() == 6

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
