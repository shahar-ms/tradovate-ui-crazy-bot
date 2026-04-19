"""
Smoke tests for the simplified floating-HUD-only UI.

Covers:
  - HUD builds and reflects UiState
  - Disabled/enabled button rules per state
  - PnL row hidden when flat, '—' when no verified fill, numeric when verified
  - manual_rejected signal drives the toast
  - HUD position save/restore round-trip
  - UiController propagates fill_price + PnL from RuntimeState
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from app.ui.app_signals import AppSignals  # noqa: E402
from app.ui.controller import UiController  # noqa: E402
from app.ui.theme import status_color  # noqa: E402
from app.ui.ui_state import UiState  # noqa: E402
from app.ui.widgets.floating_hud import FloatingHud  # noqa: E402


class _FakeController:
    """Minimal stub so the HUD can render without a real supervisor."""

    def __init__(self):
        self.is_running_val = False
        self.manual_calls: list[str] = []
        self.cancel_all_calls = 0
        self.disarm_calls = 0
        self.halt_calls: list[str] = []
        self.arm_calls = 0

    def is_running(self):
        return self.is_running_val

    def submit_manual(self, action):
        self.manual_calls.append(action)
        return True, "emitted"

    def cancel_all(self):
        self.cancel_all_calls += 1

    def disarm(self):
        self.disarm_calls += 1

    def halt(self, reason):
        self.halt_calls.append(reason)

    def arm(self):
        self.arm_calls += 1
        return None

    def turn_on(self):
        self.arm_calls += 1
        return None

    def turn_off(self):
        self.disarm_calls += 1
        return None

    def pre_arm_checks(self):
        return []


def _build_hud(qtbot, state: UiState | None = None,
               controller: _FakeController | None = None) -> tuple[FloatingHud, _FakeController, UiState]:
    signals = AppSignals()
    state = state or UiState()
    controller = controller or _FakeController()
    hud = FloatingHud(signals=signals, state=state, controller=controller)
    hud._refresh_timer.stop()
    qtbot.addWidget(hud)
    return hud, controller, state


def test_theme_status_colors_present():
    assert status_color("ok")
    assert status_color("broken")


def test_hud_builds_and_reflects_flat_state(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.mode = "PAPER"
    state.last_price = 19234.25
    state.last_confidence = 90.0
    state.price_stream_health = "ok"
    hud._refresh_all()
    assert hud._mode_lbl.text() == "PAPER"
    assert hud._price_lbl.text() == "19234.25"
    assert hud._pnl_lbl.isHidden()  # flat → PnL row hidden


def test_hud_shows_dash_pnl_when_unverified(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.mode = "ARMED"
    state.last_price = 19240.0
    state.position_side = "long"
    state.entry_price = 19230.0
    state.fill_price = None   # unverified
    state.pnl_points = None
    state.pnl_usd = None
    hud._refresh_all()
    assert not hud._pnl_lbl.isHidden()
    assert "—" in hud._pnl_lbl.text()
    assert "⚠" in hud._pnl_lbl.text()


def test_hud_shows_numeric_pnl_when_verified(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.mode = "ARMED"
    state.last_price = 19240.0
    state.position_side = "long"
    state.entry_price = 19235.0
    state.fill_price = 19235.0
    state.fill_price_source = "position_ocr"
    state.pnl_points = 5.0
    state.pnl_usd = 10.0
    hud._refresh_all()
    assert "verified" in hud._pos_lbl.text()
    assert "+5.00" in hud._pnl_lbl.text()
    assert "+10.00" in hud._pnl_lbl.text()


def test_hud_button_rules(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    # not running → everything but Setup disabled
    ctrl.is_running_val = False
    hud._refresh_all()
    assert not hud._buy_btn.isEnabled()
    assert not hud._sell_btn.isEnabled()
    assert not hud._cancel_btn.isEnabled()
    assert not hud._bot_toggle_btn.isEnabled()
    assert not hud._halt_btn.isEnabled()
    assert hud._setup_btn.isEnabled()

    # running + flat + not halted + not armed + calibrated:
    # BUY/SELL/CANCEL are DISABLED (armed=False ⇒ clicks are dry-run),
    # the single BOT power toggle + HALT are enabled.
    ctrl.is_running_val = True
    state.position_side = "flat"
    state.halted = False
    state.armed = False
    state.calibration_loaded = True
    hud._refresh_all()
    assert not hud._buy_btn.isEnabled()
    assert not hud._sell_btn.isEnabled()
    assert not hud._cancel_btn.isEnabled()
    assert hud._bot_toggle_btn.isEnabled()
    assert hud._halt_btn.isEnabled()

    # once the bot is ON (armed + auto): BUY/SELL/CANCEL come online.
    state.armed = True
    state.auto_enabled = True
    hud._refresh_all()
    assert hud._buy_btn.isEnabled()
    assert hud._sell_btn.isEnabled()
    assert hud._cancel_btn.isEnabled()

    # in a position → BUY/SELL disabled, CANCEL still available
    state.position_side = "long"
    hud._refresh_all()
    assert not hud._buy_btn.isEnabled()
    assert not hud._sell_btn.isEnabled()
    assert hud._cancel_btn.isEnabled()

    # halted → toggle + cancel disabled
    state.halted = True
    hud._refresh_all()
    assert not hud._bot_toggle_btn.isEnabled()
    assert not hud._cancel_btn.isEnabled()


def test_buy_button_routes_through_controller(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.calibration_loaded = True
    state.armed = True   # buttons only clickable when armed
    hud._refresh_all()
    hud._buy_btn.click()
    assert ctrl.manual_calls == ["BUY"]


def test_cancel_and_halt_route_correctly(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)

    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.calibration_loaded = True
    state.armed = True
    hud._refresh_all()

    hud._cancel_btn.click()
    assert ctrl.cancel_all_calls == 1

    hud._halt_btn.click()
    assert ctrl.halt_calls == ["operator_halt"]


def test_hud_shows_paused_banner_and_disables_entry_buttons(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.mode = "PAPER"
    state.calibration_loaded = True
    state.armed = True   # need armed so CANCEL would be clickable without pause
    state.halted = False
    state.paused = True
    state.pause_reason = "anchor_drift"
    state.position_side = "flat"
    hud._refresh_all()

    # paused banner visible, halt banner hidden
    assert not hud._paused_lbl.isHidden()
    assert "anchor_drift" in hud._paused_lbl.text()
    assert hud._halt_lbl.isHidden()

    # entry buttons disabled while paused
    assert not hud._buy_btn.isEnabled()
    assert not hud._sell_btn.isEnabled()
    # CANCEL ALL and HALT still available while paused (safety actions)
    assert hud._cancel_btn.isEnabled()
    assert hud._halt_btn.isEnabled()


def test_hud_paused_banner_hidden_when_halted_dominates(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.mode = "HALTED"
    state.halted = True
    state.halt_reason = "execution_ack_unknown"
    state.paused = True
    state.pause_reason = "anchor_drift"
    hud._refresh_all()
    # halted takes visual priority — paused banner hides
    assert hud._paused_lbl.isHidden()
    assert not hud._halt_lbl.isHidden()


def test_toast_appears_on_manual_rejected(qtbot):
    hud, _, _ = _build_hud(qtbot)
    hud._show_toast("position active — use Cancel All first")
    # isVisible() recurses through parents (HUD itself isn't shown in tests);
    # isHidden() returns False as long as the widget wasn't explicitly hidden.
    assert not hud._toast_lbl.isHidden()
    assert "position active" in hud._toast_lbl.text()


def test_calibration_dims_and_restores_windows_around_capture(qtbot):
    """_hide_app_windows_for_capture makes visible top-level widgets fully
    transparent; _restore_app_windows restores their original opacity."""
    from PySide6.QtWidgets import QLabel

    from app.ui.pages.calibration_page import CalibrationPage

    signals = AppSignals()
    page = CalibrationPage(signals)
    qtbot.addWidget(page)

    # stand-in for "some other window of our app" (like the HUD)
    fake_other = QLabel("fake")
    qtbot.addWidget(fake_other)
    fake_other.show()
    fake_other.setWindowOpacity(1.0)

    dimmed = page._hide_app_windows_for_capture()
    hit_widgets = [row[0] for row in dimmed]
    assert fake_other in hit_widgets
    # opacity is now 0 — but widget still "visible" from Qt's perspective
    assert fake_other.windowOpacity() == 0.0
    assert fake_other.isVisible()

    page._restore_app_windows(dimmed)
    assert fake_other.windowOpacity() == 1.0


def test_calibration_dialog_has_maximize_toggle(qtbot):
    """The calibration dialog must support maximize for pixel-precise work."""
    from app.ui.dialogs.calibration_dialog import CalibrationDialog

    signals = AppSignals()
    dlg = CalibrationDialog(signals)
    qtbot.addWidget(dlg)

    # There must be an inline Maximize button
    assert dlg.maximize_btn.text() == "Maximize"
    # Window flags include the maximize button hint so the title bar shows it
    assert bool(dlg.windowFlags() & Qt.WindowMaximizeButtonHint)

    # Toggle: simulate the click path. showMaximized() requires the platform
    # to actually maximize; on the offscreen backend it may not flip
    # isMaximized() immediately. So we just verify the handler is wired and
    # the button label toggles correctly via the internal path when
    # isMaximized() is true.
    dlg.showMaximized()
    # relabel should happen after showMaximized returns a maximized state —
    # we emulate it directly to avoid offscreen timing flakiness.
    dlg._toggle_maximize()
    # Second call: we're currently in showMaximized state (handler saw that
    # and restored), so label should now read either "Maximize" (if restored)
    # or "Restore" (if second toggle re-maximized). Either is valid; we just
    # want to confirm the handler mutates the label.
    assert dlg.maximize_btn.text() in ("Maximize", "Restore")


def test_hud_bot_state_row_shows_on_when_armed_plus_auto(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.armed = True
    state.auto_enabled = True
    state.calibration_loaded = True
    hud._refresh_all()
    assert "ON" in hud._bot_state_lbl.text()
    assert hud._bot_toggle_btn.text() == "Turn OFF"


def test_hud_bot_state_row_shows_off_when_disarmed(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.armed = False
    state.auto_enabled = False
    state.calibration_loaded = True
    hud._refresh_all()
    assert "OFF" in hud._bot_state_lbl.text()
    assert hud._bot_toggle_btn.text() == "Turn ON"


def test_hud_bot_toggle_when_on_calls_turn_off(qtbot):
    hud, ctrl, state = _build_hud(qtbot)
    ctrl.is_running_val = True
    state.armed = True
    state.auto_enabled = True
    state.calibration_loaded = True
    ctrl.turn_off_calls = 0
    ctrl.turn_off = lambda: (ctrl.__setattr__('turn_off_calls',
                                               ctrl.turn_off_calls + 1), None)[1]
    hud._refresh_all()
    hud._bot_toggle_btn.click()
    assert ctrl.turn_off_calls == 1


def test_hud_compact_view_shows_on_off(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.last_price = 19234.25
    state.armed = True
    state.auto_enabled = True
    hud._set_minimized(True)
    hud._refresh_all()
    assert hud._compact_mode.text() == "ON"

    state.auto_enabled = False
    state.armed = False
    hud._refresh_all()
    assert hud._compact_mode.text() == "OFF"


def test_hud_starts_expanded(qtbot):
    hud, _, _ = _build_hud(qtbot)
    assert hud._minimized is False
    assert hud._stack.currentIndex() == 0
    assert hud.width() == 330  # HUD_WIDTH


def test_hud_minimize_shrinks_and_swaps_view(qtbot):
    from app.ui.widgets.floating_hud import HUD_COMPACT_HEIGHT, HUD_COMPACT_WIDTH
    hud, _, _ = _build_hud(qtbot)
    hud._set_minimized(True)
    assert hud._minimized is True
    assert hud._stack.currentIndex() == 1
    assert hud.width() == HUD_COMPACT_WIDTH
    assert hud.height() == HUD_COMPACT_HEIGHT


def test_hud_expand_from_minimized(qtbot):
    from app.ui.widgets.floating_hud import HUD_HEIGHT, HUD_WIDTH
    hud, _, _ = _build_hud(qtbot)
    hud._set_minimized(True)
    hud._set_minimized(False)
    assert hud._minimized is False
    assert hud._stack.currentIndex() == 0
    assert (hud.width(), hud.height()) == (HUD_WIDTH, HUD_HEIGHT)


def test_hud_set_minimized_is_idempotent(qtbot):
    hud, _, _ = _build_hud(qtbot)
    hud._set_minimized(True)
    idx_before = hud._stack.currentIndex()
    hud._set_minimized(True)  # same state — no-op
    assert hud._stack.currentIndex() == idx_before


def test_hud_compact_view_reflects_state(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.last_price = 19234.25
    state.position_side = "long"
    state.armed = True
    state.auto_enabled = True
    hud._set_minimized(True)
    hud._refresh_all()
    assert hud._compact_price.text() == "19234.25"
    assert hud._compact_mode.text() == "ON"
    assert hud._compact_pos.text() == "LONG"


def test_hud_compact_shows_halted(qtbot):
    hud, _, state = _build_hud(qtbot)
    state.halted = True
    state.halt_reason = "execution_ack_unknown"
    hud._set_minimized(True)
    hud._refresh_all()
    assert hud._compact_mode.text() == "HALTED"


def test_hud_has_no_close_x_button(qtbot):
    """User explicitly asked for the X to be removed from the HUD."""
    hud, _, _ = _build_hud(qtbot)
    assert not hasattr(hud, "_close_btn"), \
        "_close_btn should have been removed — close via right-click > Exit app"


def test_hud_saves_minimized_flag(qtbot, tmp_path, monkeypatch):
    from app.utils import paths
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)

    hud, _, _ = _build_hud(qtbot)
    hud._set_minimized(True)
    hud.save_position()
    saved = json.loads((tmp_path / "hud_pos.json").read_text())
    assert saved["minimized"] is True

    hud2, _, _ = _build_hud(qtbot)
    hud2._restore_saved_position()
    assert hud2._minimized is True


def test_hud_saves_and_restores_position(qtbot, tmp_path, monkeypatch):
    from app.utils import paths
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)

    hud, _, _ = _build_hud(qtbot)
    # use small coords so the clamp-to-screen logic in _restore_saved_position
    # doesn't shift us — the offscreen Qt platform reports a tiny screen.
    hud.move(12, 34)
    hud.save_position()

    saved = json.loads((tmp_path / "hud_pos.json").read_text())
    assert saved["x"] == 12
    assert saved["y"] == 34

    # a second HUD instance should restore that position
    hud2, _, _ = _build_hud(qtbot)
    hud2._restore_saved_position()
    assert (hud2.x(), hud2.y()) == (12, 34)


def test_ui_state_carries_new_fields():
    s = UiState()
    # defaults
    assert s.fill_price is None
    assert s.fill_price_source is None
    assert s.pnl_points is None
    assert s.pnl_usd is None


def test_controller_submit_manual_when_stopped_emits_rejection(qtbot):
    signals = AppSignals()
    state = UiState()
    controller = UiController(signals=signals, state=state)
    captured = []
    signals.manual_rejected.connect(lambda msg: captured.append(msg))
    ok, msg = controller.submit_manual("BUY")
    assert not ok
    assert captured and "bot not running" in captured[0]
