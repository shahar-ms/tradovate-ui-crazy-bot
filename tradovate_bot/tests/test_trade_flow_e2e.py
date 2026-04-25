"""
End-to-end trade-flow tests.

Mimics a full live trade from the supervisor's POV: an entry signal lands,
the broker fills (size + entry price arrive via the OCR watchers), price
ticks during the trade, the position closes, and we verify the realized
PnL matches a hand-rolled expected value.

Why this exists:
  Once you've got entry_price + signed position size + current price, the
  whole P&L story is mechanical. Driving the supervisor's three handlers
  directly — the same ones the real watchers + price stream call — gives
  us a screen-free, OCR-free harness for the full lifecycle. Future flows
  (trailing stops, scale-in / scale-out at different prices, side flips)
  slot in by extending TradeFlow rather than re-plumbing infrastructure.
"""

from __future__ import annotations

import pytest

from app.orchestrator.supervisor import Supervisor
from app.orchestrator.trade_flow import TradeFlow
from app.strategy.engine import StrategyEngine

from .test_supervisor import FakeExecutor, _make_supervisor, _strategy_cfg

# Lazy/optional Qt imports — only the HUD-driven tests need them, and the
# rest of this file should still run if Qt is unavailable for any reason.
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("pyautogui")

import os                                                       # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.ui.app_signals import AppSignals                       # noqa: E402
from app.ui.controller import UiController                      # noqa: E402
from app.ui.ui_state import UiState                             # noqa: E402

# ============================================================
# Profit + loss E2E lifecycles
# ============================================================


def test_e2e_long_winning_trade():
    """Long 1 contract @ 26680.00, runs up to 26700.50, manual close.
    Expected realized PnL: +20.50 pts * $2/pt * 1 = $41.00 USD (MNQ)."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    # Pre-trade tick — sanity check that flat state has no PnL.
    flow.tick(26680.00)
    assert flow.latest.side == "flat"
    assert flow.latest.pnl_usd is None

    # Broker fills the BUY.
    flow.open("long", entry=26680.00, size=1)
    assert flow.latest.side == "long"
    assert flow.latest.fill == 26680.00

    # Price ticks during the trade — unrealized PnL should track linearly.
    flow.tick(26690.00)
    assert flow.latest.pnl_points == pytest.approx(10.00)
    assert flow.latest.pnl_usd == pytest.approx(20.00)

    flow.tick(26700.50)
    assert flow.latest.pnl_points == pytest.approx(20.50)
    assert flow.latest.pnl_usd == pytest.approx(41.00)

    # Close at the last tick price.
    flow.close()
    assert flow.latest.side == "flat"
    assert flow.latest.fill is None
    assert flow.latest.pnl_usd is None, "PnL line must hide on flat"

    # Realized PnL = the unrealized at the last in-position snapshot.
    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(20.50)
    assert usd == pytest.approx(41.00)


def test_e2e_short_losing_trade():
    """Short 2 contracts @ 26700.00, market runs against us to 26710.25,
    manual close. Expected realized PnL: -10.25 pts * $2/pt * 2 = -$41.00."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    flow.tick(26700.00)
    flow.open("short", entry=26700.00, size=2)
    assert flow.latest.side == "short"
    assert flow.latest.size == 2
    assert flow.latest.pnl_points == pytest.approx(0.00)

    # Adverse moves on a short = positive price deltas = negative PnL.
    flow.tick(26705.00)
    assert flow.latest.pnl_points == pytest.approx(-5.00)
    assert flow.latest.pnl_usd == pytest.approx(-20.00)   # 2 contracts

    flow.tick(26710.25)
    assert flow.latest.pnl_points == pytest.approx(-10.25)
    assert flow.latest.pnl_usd == pytest.approx(-41.00)

    flow.close()
    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(-10.25)
    assert usd == pytest.approx(-41.00)


def test_e2e_long_winning_trade_with_strategy_pending_entry():
    """Same as test_e2e_long_winning_trade, but the engine is in
    PENDING_ENTRY when the broker fills (i.e. the strategy fired BUY and
    the watcher confirms it) — the engine should transition to LONG and
    record the entry price the watcher saw."""
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("BUY", trigger_price=26680.00,
                                  stop=26670.00, target=26700.00)

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    flow = TradeFlow(sup)

    flow.open("long", entry=26680.00, size=1)
    assert engine.state.state == "LONG", "engine should confirm pending entry"
    assert engine.state.position.entry_price == 26680.00

    flow.tick(26695.00)
    flow.close()

    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(15.00)
    assert usd == pytest.approx(30.00)


# ============================================================
# Sanity: scaffold supports flips + scale-ins (groundwork for
# trailing-stop / scale-out tests later)
# ============================================================


def test_e2e_side_flip_resets_fill_and_engine():
    """Flip from long 1 @ 26680 to short 1 @ 26700 with no flat between.
    After flip: side=short, fill cleared (entry_price watcher refreshes
    next), engine synced out of LONG."""
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("BUY", trigger_price=26680.00,
                                  stop=26670.00, target=26700.00)

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    flow = TradeFlow(sup)
    flow.open("long", entry=26680.00, size=1)
    assert engine.state.state == "LONG"

    # Direct reversal: signed size flips sign WITHOUT going through 0.
    flow.scale(-1)
    assert flow.latest.side == "short"
    assert flow.latest.fill is None, "stale long-entry must be cleared on flip"
    assert engine.state.state == "FLAT"

    # New entry-price arrives shortly after via its own watcher.
    sup._on_entry_price_changed(26700.00)
    flow.tick(26695.00)
    assert flow.latest.side == "short"
    assert flow.latest.pnl_points == pytest.approx(5.00)   # short profits as price drops


def test_e2e_scale_in_doubles_usd_pnl():
    """Open 1 @ 26680, ride to +10 pts, scale to 2 contracts at the same
    avg, ride another +5 pts, close. Expected: 10 pts on 1 contract +
    5 pts on 2 contracts = $20 + $20 = $40."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    flow.open("long", entry=26680.00, size=1)
    flow.tick(26690.00)
    assert flow.latest.pnl_usd == pytest.approx(20.00)

    flow.scale(new_size_signed=2)   # avg unchanged for the test simplicity
    flow.tick(26695.00)
    # 15 pts * $2/pt * 2 contracts = $60
    assert flow.latest.pnl_usd == pytest.approx(60.00)

    flow.close()
    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(15.00)
    assert usd == pytest.approx(60.00)


# ============================================================
# HUD-triggered E2E (operator clicks BUY/SELL on the HUD; the click
# path goes through the real UiController, with pyautogui mocked so
# nothing actually clicks the screen)
# ============================================================


def _wire_controller(sup: Supervisor) -> tuple[UiController, AppSignals, UiState]:
    """Build a real UiController and attach it to `sup` without booting
    bootstrap() — saves us from spinning up a real screen_map / executor /
    engine when the supervisor fixture has already done the equivalent."""
    signals = AppSignals()
    ui_state = UiState()
    controller = UiController(signals=signals, state=ui_state)
    controller._supervisor = sup  # bypass start() — sup already wired
    return controller, signals, ui_state


def test_e2e_hud_buy_click_drives_long_winning_trade(qtbot, monkeypatch):
    """Full operator flow: HUD BUY click -> pyautogui fires (captured) ->
    Tradovate fills (we mimic via the watcher handlers) -> price ticks ->
    operator closes from the HUD too -> realized PnL matches."""
    captured_clicks: list[tuple[int, int]] = []
    # pyautogui.click is imported lazily inside controller.hud_click; the
    # monkeypatch lands on the module-level function in sys.modules so the
    # lazy import sees our stub.
    monkeypatch.setattr("pyautogui.click",
                        lambda x, y, **_kw: captured_clicks.append((x, y)))

    sup = _make_supervisor(FakeExecutor())
    qtbot.addWidget = qtbot.addWidget if hasattr(qtbot, "addWidget") else None
    controller, _signals, _ui_state = _wire_controller(sup)
    flow = TradeFlow(sup, controller=controller)

    # Operator presses BUY on the HUD. Two consequences:
    #   1. The click reaches Tradovate (here: lands on captured_clicks).
    #   2. The supervisor records last_manual_click_action so a later
    #      OCR mismatch can be diagnosed.
    flow.hud_click("BUY")
    sm = sup.deps.screen_map
    assert captured_clicks == [(sm.buy_point.x, sm.buy_point.y)]
    assert sup.state.last_manual_click_action == "BUY"

    # Tradovate now fills our order. In production this happens because of
    # the click that just fired; in the test we mimic the broker side via
    # the same watcher callbacks the live OCR threads call.
    flow.open("long", entry=26680.00, size=1)
    assert flow.latest.side == "long"

    # Live ticks during the trade.
    flow.tick(26690.00)
    flow.tick(26700.50)
    assert flow.latest.pnl_points == pytest.approx(20.50)

    # Operator presses CANCEL ALL on the HUD to flatten.
    flow.hud_click("CANCEL_ALL")
    assert captured_clicks[-1] == (sm.cancel_all_point.x, sm.cancel_all_point.y)
    flow.close()

    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(20.50)
    assert usd == pytest.approx(41.00)


def test_e2e_hud_sell_click_drives_short_losing_trade(qtbot, monkeypatch):
    """Same end-to-end shape, but a SELL click and the market goes
    against us. Demonstrates the loss path through the HUD."""
    captured_clicks: list[tuple[int, int]] = []
    monkeypatch.setattr("pyautogui.click",
                        lambda x, y, **_kw: captured_clicks.append((x, y)))

    sup = _make_supervisor(FakeExecutor())
    controller, *_ = _wire_controller(sup)
    flow = TradeFlow(sup, controller=controller)

    flow.hud_click("SELL")
    sm = sup.deps.screen_map
    assert captured_clicks[-1] == (sm.sell_point.x, sm.sell_point.y)
    assert sup.state.last_manual_click_action == "SELL"

    flow.open("short", entry=26700.00, size=1)
    flow.tick(26715.25)             # adverse for a short
    assert flow.latest.pnl_points == pytest.approx(-15.25)

    flow.hud_click("CANCEL_ALL")
    flow.close()

    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(-15.25)
    assert usd == pytest.approx(-30.50)


# ============================================================
# Sketch: how trailing-stop tests slot into this scaffold
# ============================================================


@pytest.mark.skip(
    reason="No auto-trailing-stop yet; this test documents the shape of "
           "the eventual test once the engine grows that feature."
)
def test_e2e_trailing_stop_locks_in_partial_gain():  # pragma: no cover
    """When trailing stop ships, the test is the same harness — drive
    ticks up, then ticks back down past the trailing distance, assert that
    a SELL exit intent is emitted by the engine, and that close() lands at
    the trail-stopped price (not the high)."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)
    flow.open("long", entry=26680.00, size=1)
    flow.tick(26710.00)   # +30 high
    flow.tick(26695.00)   # pulls back; at trailing distance, expect exit signal
    # ... once engine emits EXIT_LONG on this tick:
    # flow.close()
    # pts, _ = flow.realized_pnl()
    # assert pts == pytest.approx(15.00)   # locked in 15 of the 30 pts
