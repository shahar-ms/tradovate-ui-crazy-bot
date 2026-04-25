"""
Direct tests for the in-trade TradePanel widget. Drives `apply_state`
with hand-built UiState snapshots and asserts the right widgets are
populated / shown / colored.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from app.ui.theme import BROKEN_RED, OK_GREEN  # noqa: E402
from app.ui.ui_state import UiState                 # noqa: E402
from app.ui.widgets.trade_panel import TradePanel   # noqa: E402


def _make_panel(qtbot) -> TradePanel:
    panel = TradePanel()
    qtbot.addWidget(panel)
    return panel


def _flat_state() -> UiState:
    s = UiState()
    s.position_side = "flat"
    return s


def _long_state(entry: float = 26680.50, last: float = 26700.50,
                size: int = 1, with_fill: bool = True) -> UiState:
    s = UiState()
    s.position_side = "long"
    s.position_size = size
    s.entry_price = entry
    s.last_price = last
    if with_fill:
        s.fill_price = entry
        s.fill_price_source = "position_ocr"
        s.pnl_points = last - entry
        s.pnl_usd = (last - entry) * 2.0 * size       # MNQ
    return s


# ------------------ slim / full toggle ------------------ #


def test_panel_shows_slim_header_when_flat(qtbot):
    panel = _make_panel(qtbot)
    panel.apply_state(_flat_state())
    assert panel.is_flat_view, "flat state must collapse to slim header"
    assert "FLAT" in panel._slim_lbl.text()


def test_panel_renders_full_grid_when_in_position(qtbot):
    panel = _make_panel(qtbot)
    s = _long_state(size=1)
    s.stop_price = 26670.00
    s.target_price = 26700.00
    panel.apply_state(s)

    assert not panel.is_flat_view
    assert panel._side_chip.text() == "LONG"
    assert panel._size_val.text() == "1"
    assert panel._entry_val.text() == "26680.50"
    assert panel._current_val.text() == "26700.50"
    assert panel._stop_val.text() == "26670.00"
    assert panel._target_val.text() == "26700.00"


# ------------------ PnL color coding ------------------ #


def test_panel_color_codes_pnl_green_on_profit(qtbot):
    panel = _make_panel(qtbot)
    panel.apply_state(_long_state(entry=26680.0, last=26700.0))
    style = panel._pnl_usd.styleSheet()
    assert OK_GREEN in style, f"profit PnL must be green; got: {style}"
    assert "+40.00" in panel._pnl_usd.text()


def test_panel_color_codes_pnl_red_on_loss(qtbot):
    panel = _make_panel(qtbot)
    panel.apply_state(_long_state(entry=26700.0, last=26680.0))
    style = panel._pnl_usd.styleSheet()
    assert BROKEN_RED in style, f"losing PnL must be red; got: {style}"
    assert "-40.00" in panel._pnl_usd.text()


# ------------------ verified tag ------------------ #


def test_panel_verified_tag_appears_only_for_position_ocr_source(qtbot):
    panel = _make_panel(qtbot)
    # isHidden() is reliable in offscreen Qt (isVisible() recurses to root,
    # which isn't shown in unit tests).
    panel.apply_state(_long_state(with_fill=True))
    assert not panel._verified_chip.isHidden()

    # Without position_ocr source (e.g. raw HUD click without entry-price
    # watcher firing yet): chip hidden.
    s = _long_state(with_fill=False)
    s.fill_price_source = None
    panel.apply_state(s)
    assert panel._verified_chip.isHidden()


# ------------------ stop / target absent ------------------ #


def test_panel_dashes_stop_and_target_when_engine_has_none(qtbot):
    """Raw HUD-click trades have no engine-driven stop / target. Those
    rows must show '—' rather than disappear or display 0."""
    panel = _make_panel(qtbot)
    s = _long_state()
    s.stop_price = None
    s.target_price = None
    panel.apply_state(s)
    assert panel._stop_val.text() == "—"
    assert panel._target_val.text() == "—"


# ------------------ short side ------------------ #


def test_panel_short_side_chip_uses_red(qtbot):
    panel = _make_panel(qtbot)
    s = _long_state()
    s.position_side = "short"
    s.entry_price = 26700.00
    s.last_price = 26695.00
    s.fill_price = 26700.00
    s.pnl_points = 5.00
    s.pnl_usd = 10.00
    panel.apply_state(s)

    assert panel._side_chip.text() == "SHORT"
    assert BROKEN_RED in panel._side_chip.styleSheet()
