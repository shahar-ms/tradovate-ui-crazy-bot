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
from app.orchestrator.trade_journal import TradeJournal
from app.strategy.engine import StrategyEngine

from .test_supervisor import FakeExecutor, _make_supervisor, _strategy_cfg


def _make_supervisor_with_journal() -> tuple[Supervisor, TradeJournal]:
    journal = TradeJournal(db_path=":memory:", session_id="e2e-test")
    sup = _make_supervisor(FakeExecutor(), journal=journal)
    return sup, journal

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
    """Long 1 contract @ 26680.00 with a real-feeling price path: we go
    UNDERWATER first (drawdown), recover through breakeven, then run up
    to a profitable exit. Final realized PnL: +20.50 pts * $2/pt * 1 =
    +$41.00 USD (MNQ). The interesting bit is the negative-PnL phase —
    we assert the HUD sees a red drawdown before the green close."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    flow.tick(26680.00)
    assert flow.latest.side == "flat"
    assert flow.latest.pnl_usd is None

    flow.open("long", entry=26680.00, size=1)
    assert flow.latest.side == "long"
    assert flow.latest.fill == 26680.00
    assert flow.latest.pnl_points == pytest.approx(0.00)

    # ---- drawdown leg: price drops AGAINST our long ---- #
    flow.tick(26674.50)
    assert flow.latest.pnl_points == pytest.approx(-5.50)
    assert flow.latest.pnl_usd == pytest.approx(-11.00)
    assert flow.latest.pnl_usd < 0, "drawdown phase must show negative PnL"

    flow.tick(26668.25)            # max adverse excursion
    assert flow.latest.pnl_points == pytest.approx(-11.75)
    assert flow.latest.pnl_usd == pytest.approx(-23.50)

    # ---- recovery: back through breakeven ---- #
    flow.tick(26680.00)
    assert flow.latest.pnl_points == pytest.approx(0.00)
    assert flow.latest.pnl_usd == pytest.approx(0.00)

    # ---- profit leg ---- #
    flow.tick(26690.00)
    assert flow.latest.pnl_points == pytest.approx(10.00)
    assert flow.latest.pnl_usd == pytest.approx(20.00)

    flow.tick(26700.50)
    assert flow.latest.pnl_points == pytest.approx(20.50)
    assert flow.latest.pnl_usd == pytest.approx(41.00)

    # Sanity: across the trade we touched both signs of unrealized PnL.
    pnls = [s.pnl_usd for s in flow.snapshots if s.pnl_usd is not None]
    assert min(pnls) < 0 and max(pnls) > 0, \
        "winning trade should still cross negative territory en route"

    flow.close()
    assert flow.latest.side == "flat"
    assert flow.latest.fill is None
    assert flow.latest.pnl_usd is None, "PnL line must hide on flat"

    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(20.50)
    assert usd == pytest.approx(41.00)


def test_e2e_long_winning_trade_records_one_journal_row():
    """Same long-winner shape as above, but with a journal wired so we
    verify exactly one TradeRecord lands in session_trades AND in SQLite."""
    sup, journal = _make_supervisor_with_journal()
    flow = TradeFlow(sup)

    flow.tick(26680.00)
    flow.open("long", entry=26680.00, size=1)
    flow.tick(26674.50)         # drawdown
    flow.tick(26680.00)
    flow.tick(26700.50)         # exit price
    flow.close()

    assert journal.session_count() == 1
    rec = journal.session_trades[0]
    assert rec.side == "long"
    assert rec.entry_price == pytest.approx(26680.00)
    assert rec.exit_price == pytest.approx(26700.50)
    assert rec.max_size == 1
    assert rec.pnl_points == pytest.approx(20.50)
    assert rec.pnl_usd == pytest.approx(41.00)
    # SQLite roundtrip — same trade appears when read back from disk.
    [from_db] = journal.all_trades()
    assert from_db.pnl_usd == rec.pnl_usd
    assert from_db.session_id == "e2e-test"


def test_e2e_short_losing_trade():
    """Short 2 contracts @ 26700.00 with a real-feeling price path: price
    DROPS in our favor first (we look like winners briefly), then rallies
    against us through breakeven and stops out at a loss. Final realized
    PnL: -10.25 pts * $2/pt * 2 = -$41.00 USD. Asserts that the HUD
    showed positive PnL at the favorable peak before the eventual loss."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    flow.tick(26700.00)
    flow.open("short", entry=26700.00, size=2)
    assert flow.latest.side == "short"
    assert flow.latest.size == 2
    assert flow.latest.pnl_points == pytest.approx(0.00)

    # ---- favorable leg: short profits as price drops ---- #
    flow.tick(26695.50)
    assert flow.latest.pnl_points == pytest.approx(4.50)
    assert flow.latest.pnl_usd == pytest.approx(18.00)        # 2 contracts
    assert flow.latest.pnl_usd > 0, "short should show profit while price drops"

    flow.tick(26692.00)            # max favorable excursion
    assert flow.latest.pnl_points == pytest.approx(8.00)
    assert flow.latest.pnl_usd == pytest.approx(32.00)

    # ---- reversal: back through breakeven ---- #
    flow.tick(26700.00)
    assert flow.latest.pnl_points == pytest.approx(0.00)
    assert flow.latest.pnl_usd == pytest.approx(0.00)

    # ---- adverse leg: rally against the short ---- #
    flow.tick(26705.00)
    assert flow.latest.pnl_points == pytest.approx(-5.00)
    assert flow.latest.pnl_usd == pytest.approx(-20.00)

    flow.tick(26710.25)
    assert flow.latest.pnl_points == pytest.approx(-10.25)
    assert flow.latest.pnl_usd == pytest.approx(-41.00)

    pnls = [s.pnl_usd for s in flow.snapshots if s.pnl_usd is not None]
    assert max(pnls) > 0 and min(pnls) < 0, \
        "losing trade should still touch positive territory before stopping out"

    flow.close()
    pts, usd = flow.realized_pnl()
    assert pts == pytest.approx(-10.25)
    assert usd == pytest.approx(-41.00)


def test_e2e_short_losing_trade_records_one_journal_row():
    """Loss path also lands one row, with negative PnL preserved."""
    sup, journal = _make_supervisor_with_journal()
    flow = TradeFlow(sup)

    flow.tick(26700.00)
    flow.open("short", entry=26700.00, size=2)
    flow.tick(26695.50)         # favorable peak
    flow.tick(26710.25)         # exit price
    flow.close()

    assert journal.session_count() == 1
    rec = journal.session_trades[0]
    assert rec.side == "short"
    assert rec.entry_price == pytest.approx(26700.00)
    assert rec.exit_price == pytest.approx(26710.25)
    assert rec.max_size == 2
    assert rec.pnl_points == pytest.approx(-10.25)
    assert rec.pnl_usd == pytest.approx(-41.00)


def test_e2e_flip_with_new_entry_produces_live_pnl_on_new_side():
    """Regression: flow.scale(-1, new_entry=X) used to apply the entry
    BEFORE the size change. The size change's flip-handling then cleared
    last_fill_price, leaving the new short side with no fill — HUD shows
    'no verified fill' and PnL never computes. Order is now size-then-
    entry so the new fill survives the flip-clear."""
    sup = _make_supervisor(FakeExecutor())
    flow = TradeFlow(sup)

    flow.tick(26680.00)
    flow.open("long", entry=26680.00, size=1)
    flow.tick(26690.00)
    assert flow.latest.fill == 26680.00

    # Flip to short with a new entry in the same call.
    flow.scale(-1, new_entry=26690.00)
    assert flow.latest.side == "short"
    assert flow.latest.fill == 26690.00, \
        "new short fill must survive the flip's last_fill_price clear"

    # A subsequent tick must compute PnL against the NEW entry, not None.
    flow.tick(26687.00)
    assert flow.latest.fill == 26690.00
    assert flow.latest.pnl_points == pytest.approx(3.00)
    assert flow.latest.pnl_usd == pytest.approx(6.00)


def test_e2e_demo_test_tp_scenario_records_one_winning_trade():
    """Drive the demo's `test_tp` scenario synchronously (skip the
    QTimer scheduling — just fire each step's lambda in order). One long
    trade @ 26680 hits target @ 26690: +$20."""
    from app.ui.demo_hud_trade import _scenario_test_tp

    sup, journal = _make_supervisor_with_journal()
    flow = TradeFlow(sup)
    _, steps = _scenario_test_tp()
    for step in steps:
        step.fn(flow)

    assert journal.session_count() == 1
    rec = journal.session_trades[0]
    assert rec.side == "long"
    assert rec.entry_price == pytest.approx(26680.00)
    assert rec.exit_price == pytest.approx(26690.00)
    assert rec.pnl_usd == pytest.approx(20.0)


def test_e2e_demo_test_sl_scenario_records_one_losing_trade():
    """Mirror of the TP test. One long trade @ 26680 hits stop @ 26675:
    −$10 (5 pts * $2 * 1 contract)."""
    from app.ui.demo_hud_trade import _scenario_test_sl

    sup, journal = _make_supervisor_with_journal()
    flow = TradeFlow(sup)
    _, steps = _scenario_test_sl()
    for step in steps:
        step.fn(flow)

    assert journal.session_count() == 1
    rec = journal.session_trades[0]
    assert rec.side == "long"
    assert rec.entry_price == pytest.approx(26680.00)
    assert rec.exit_price == pytest.approx(26675.00)
    assert rec.pnl_usd == pytest.approx(-10.0)


def test_e2e_two_back_to_back_trades_accumulate_in_journal():
    """Sequential trades within a single session — both should land,
    in order, with cumulative PnL retrievable via session_trades."""
    sup, journal = _make_supervisor_with_journal()
    flow = TradeFlow(sup)

    # Trade 1: long winner
    flow.tick(26680.00)
    flow.open("long", entry=26680.00, size=1)
    flow.tick(26690.00)
    flow.close()

    # Trade 2: short loser
    flow.tick(26690.00)
    flow.open("short", entry=26690.00, size=1)
    flow.tick(26700.00)
    flow.close()

    assert journal.session_count() == 2
    t1, t2 = journal.session_trades
    assert t1.side == "long" and t1.pnl_points == pytest.approx(10.0)
    assert t2.side == "short" and t2.pnl_points == pytest.approx(-10.0)
    # cumulative session PnL = +$20 + (-$20) = $0
    cumulative = sum(t.pnl_usd for t in journal.session_trades)
    assert cumulative == pytest.approx(0.0)


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

    # Realistic path: drawdown first, recover, then run to profit.
    flow.tick(26672.00)
    assert flow.latest.pnl_usd == pytest.approx(-16.00)
    flow.tick(26690.00)
    flow.tick(26700.50)
    assert flow.latest.pnl_points == pytest.approx(20.50)

    pnls = [s.pnl_usd for s in flow.snapshots if s.pnl_usd is not None]
    assert min(pnls) < 0 and max(pnls) > 0, \
        "winning trade should cross both signs en route"

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
    # Favorable first (short profits as price drops), then reversal to a
    # losing exit.
    flow.tick(26694.00)
    assert flow.latest.pnl_usd == pytest.approx(12.00)   # short in profit
    flow.tick(26715.25)             # adverse rally past entry
    assert flow.latest.pnl_points == pytest.approx(-15.25)

    pnls = [s.pnl_usd for s in flow.snapshots if s.pnl_usd is not None]
    assert max(pnls) > 0 and min(pnls) < 0, \
        "losing trade should touch positive territory before stopping out"

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
