"""
End-to-end tests on the strategy engine. Drives it with synthetic ticks,
uses a mocked session clock so the session gate always passes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pytest

from app.capture.models import PriceTick
from app.models.config import SessionWindow, StrategyConfig
from app.strategy.engine import StrategyEngine
from app.strategy.models import SignalActionT, SignalIntent


def _in_session_cfg(**overrides) -> StrategyConfig:
    base = {
        "bar_seconds": 1,
        "level_lookback_bars": 60,
        "level_touch_tolerance_points": 0.5,
        "min_touches_for_level": 2,
        "sweep_break_distance_points": 1.0,
        "sweep_return_timeout_bars": 5,
        "stop_loss_points": 5.0,
        "take_profit_points": 10.0,
        "time_stop_bars": 20,
        "cooldown_bars_after_exit": 0,
        "max_trades_per_session": 5,
        "max_consecutive_losses": 3,
        "cancel_all_before_new_entry": True,
        "session_windows": [SessionWindow(start="00:00", end="23:59", timezone="UTC")],
    }
    base.update(overrides)
    return StrategyConfig(**base)


def _tick(ts_ms: int, price: float, frame_id: int = 0) -> PriceTick:
    return PriceTick(ts_ms=ts_ms, frame_id=frame_id, raw_text=f"{price}",
                     price=price, confidence=95.0, accepted=True)


def _engine(cfg: StrategyConfig) -> tuple[StrategyEngine, list[SignalIntent]]:
    intents: list[SignalIntent] = []
    eng = StrategyEngine(cfg, emit=intents.append,
                         now_utc=lambda: datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc))
    return eng, intents


def _feed(eng: StrategyEngine, prices_per_sec: list[list[float]], start_ts: int = 1_700_000_000_000):
    """Feed ticks grouped by second."""
    ts = start_ts
    fid = 0
    for second in prices_per_sec:
        for p in second:
            fid += 1
            eng.on_tick(_tick(ts, p, fid))
            ts += 50
        # jump to next whole second
        ts = (ts // 1000 + 1) * 1000


def _build_resistance_bars_then_sweep(resistance: float, n_setup: int = 10):
    """
    Build alternating swing highs near `resistance`, then one sweep bar and
    one failed-close bar. Each inner list is the ticks in one second.
    """
    seconds: list[list[float]] = []
    # setup: alternating swings so detector finds a resistance
    highs = [resistance - 1, resistance - 2, resistance, resistance - 2,
             resistance - 1, resistance - 2, resistance, resistance - 2,
             resistance - 1, resistance - 2]
    for h in highs[:n_setup]:
        seconds.append([h - 1, h, h - 0.5])

    # sweep bar: high well above resistance
    seconds.append([resistance - 0.5, resistance + 2.0, resistance + 1.0])
    # failed close: closes back below resistance
    seconds.append([resistance + 1.0, resistance + 0.5, resistance - 1.0])
    # trailing tick to force the fail-close bar to actually close
    seconds.append([resistance - 1.0])
    return seconds


def test_engine_emits_short_entry_on_sweep_reversal():
    cfg = _in_session_cfg(min_touches_for_level=2, cooldown_bars_after_exit=0,
                          take_profit_points=20.0, stop_loss_points=20.0)
    eng, intents = _engine(cfg)

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)

    actions = [i.action for i in intents]
    assert "SELL" in actions
    # CANCEL_ALL must precede the SELL
    sell_idx = actions.index("SELL")
    assert "CANCEL_ALL" in actions[:sell_idx]


def test_engine_blocks_entries_when_price_stream_not_ok():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    eng.set_price_stream_ok(False)

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)
    assert all(i.action not in ("BUY", "SELL") for i in intents)


def test_engine_blocks_entries_when_halted():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    eng.halt("test")

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)
    assert all(i.action not in ("BUY", "SELL") for i in intents)


def test_engine_exits_on_stop_loss():
    cfg = _in_session_cfg(stop_loss_points=2.0, take_profit_points=50.0,
                          time_stop_bars=1000)
    eng, intents = _engine(cfg)

    # force into a short position manually
    eng.state.to_pending_entry("SELL", 100.0, 102.0, 90.0)
    eng.state.confirm_entry(100.0)

    # tick that hits the stop
    eng.on_tick(_tick(1_700_000_000_500, 103.0, 1))
    assert any(i.action == "EXIT_SHORT" and i.reason == "stop_loss" for i in intents)


def test_engine_exits_on_take_profit_long():
    cfg = _in_session_cfg(stop_loss_points=5.0, take_profit_points=5.0, time_stop_bars=1000)
    eng, intents = _engine(cfg)

    eng.state.to_pending_entry("BUY", 100.0, 95.0, 105.0)
    eng.state.confirm_entry(100.0)

    eng.on_tick(_tick(1_700_000_000_500, 105.5, 1))
    assert any(i.action == "EXIT_LONG" and i.reason == "take_profit" for i in intents)


def test_engine_cancel_all_before_entry_flag():
    cfg = _in_session_cfg(cancel_all_before_new_entry=False, min_touches_for_level=2)
    eng, intents = _engine(cfg)

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)

    actions = [i.action for i in intents]
    if "SELL" in actions:
        sell_idx = actions.index("SELL")
        assert "CANCEL_ALL" not in actions[:sell_idx]


def test_manual_intent_buy_from_flat_emits_entry():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    # seed a last accepted price (normally set by on_tick)
    eng._last_accepted_price = 100.0

    ok, msg, returned = eng.submit_manual_intent("BUY", reason="hud")
    assert ok
    # the 3-tuple form returns the same intents via _emit_cb too
    actions = [i.action for i in returned]
    assert "BUY" in actions
    # with cancel_all_before_new_entry=True by default, CANCEL_ALL should precede BUY
    buy_idx = actions.index("BUY")
    assert "CANCEL_ALL" in actions[:buy_idx]
    assert eng.state.state == "PENDING_ENTRY"
    # _emit_cb-wired list in the test fixture should also have received them
    assert len(intents) == len(returned)


def test_manual_intent_rejected_when_in_position():
    cfg = _in_session_cfg()
    eng, _ = _engine(cfg)
    eng._last_accepted_price = 100.0
    # put into long manually
    eng.state.to_pending_entry("BUY", 100.0, 95.0, 110.0)
    eng.state.confirm_entry(100.0)

    ok, msg, _ = eng.submit_manual_intent("BUY", reason="hud_double")
    assert not ok
    assert "position active" in msg.lower()


def test_manual_intent_rejected_when_halted():
    cfg = _in_session_cfg()
    eng, _ = _engine(cfg)
    eng._last_accepted_price = 100.0
    eng.halt("test_halt")
    ok, msg, _ = eng.submit_manual_intent("BUY", reason="hud")
    assert not ok
    assert msg == "halted"


def test_manual_intent_rejected_when_price_stream_not_ok():
    cfg = _in_session_cfg()
    eng, _ = _engine(cfg)
    eng._last_accepted_price = 100.0
    eng.set_price_stream_ok(False)
    ok, msg, _ = eng.submit_manual_intent("BUY", reason="hud")
    assert not ok
    assert "price stream" in msg.lower()


def test_manual_cancel_all_always_emits_when_not_halted():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    eng._last_accepted_price = 100.0
    ok, _, returned = eng.submit_manual_intent("CANCEL_ALL", reason="hud_cancel")
    assert ok
    assert any(i.action == "CANCEL_ALL" for i in returned)


def test_manual_exit_long_only_when_long():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    eng._last_accepted_price = 100.0

    # from FLAT: reject
    ok, msg, _ = eng.submit_manual_intent("EXIT_LONG", reason="hud_exit")
    assert not ok

    # transition into LONG
    eng.state.to_pending_entry("BUY", 100.0, 95.0, 110.0)
    eng.state.confirm_entry(100.0)

    ok, _, returned = eng.submit_manual_intent("EXIT_LONG", reason="hud_exit")
    assert ok
    assert any(i.action == "EXIT_LONG" for i in returned)


def test_engine_auto_disabled_blocks_auto_entries():
    """When auto_enabled is False, bar-driven entries must not fire."""
    cfg = _in_session_cfg(min_touches_for_level=2)
    eng, intents = _engine(cfg)
    eng.auto_enabled = False

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)

    # No BUY/SELL should be emitted while auto is off
    actions = [i.action for i in intents]
    assert "BUY" not in actions
    assert "SELL" not in actions


def test_engine_auto_disabled_blocks_stop_loss_exits():
    """Auto exits (stop loss) must not fire when auto_enabled is False."""
    cfg = _in_session_cfg(stop_loss_points=2.0, take_profit_points=50.0,
                          time_stop_bars=1000)
    eng, intents = _engine(cfg)
    eng.auto_enabled = False

    # force into a short position manually
    eng.state.to_pending_entry("SELL", 100.0, 102.0, 90.0)
    eng.state.confirm_entry(100.0)

    # tick that would have hit the stop
    eng.on_tick(_tick(1_700_000_000_500, 103.0, 1))
    # no exit fired because auto is off — operator must exit manually
    assert not any(i.action == "EXIT_SHORT" for i in intents)


def test_engine_auto_disabled_still_allows_manual_intents():
    """Manual intents must work even when auto is disabled."""
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)
    eng.auto_enabled = False
    eng._last_accepted_price = 100.0

    ok, _, returned = eng.submit_manual_intent("BUY", reason="hud")
    assert ok
    assert any(i.action == "BUY" for i in returned)


def test_engine_no_double_entry_while_in_position():
    cfg = _in_session_cfg()
    eng, intents = _engine(cfg)

    # put into long
    eng.state.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    eng.state.confirm_entry(100.0)

    seconds = _build_resistance_bars_then_sweep(resistance=100.0, n_setup=10)
    _feed(eng, seconds)

    # while in position, no BUY/SELL entries emitted
    entry_intents = [i for i in intents if i.action in ("BUY", "SELL")]
    assert entry_intents == []
