import pytest

from app.strategy.state_machine import InvalidTransition, StrategyStateMachine


def test_starts_flat():
    sm = StrategyStateMachine()
    assert sm.is_flat()


def test_entry_lifecycle_long():
    sm = StrategyStateMachine()
    sm.to_pending_entry("BUY", trigger_price=100.0, stop=95.0, target=112.0)
    assert sm.state == "PENDING_ENTRY"
    assert sm.position.side == "long"
    sm.confirm_entry(fill_price=100.25)
    assert sm.state == "LONG"
    assert sm.position.entry_price == 100.25


def test_entry_lifecycle_short():
    sm = StrategyStateMachine()
    sm.to_pending_entry("SELL", trigger_price=100.0, stop=105.0, target=88.0)
    sm.confirm_entry()
    assert sm.state == "SHORT"


def test_exit_lifecycle():
    sm = StrategyStateMachine()
    sm.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sm.confirm_entry(100.0)
    sm.to_pending_exit()
    assert sm.state == "PENDING_EXIT"
    sm.confirm_exit()
    assert sm.state == "FLAT"
    assert sm.position.side == "flat"


def test_cannot_enter_from_long():
    sm = StrategyStateMachine()
    sm.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sm.confirm_entry()
    with pytest.raises(InvalidTransition):
        sm.to_pending_entry("SELL", 101.0, 106.0, 90.0)


def test_halt_and_resume():
    sm = StrategyStateMachine()
    sm.halt("too much loss")
    assert sm.is_halted()
    assert sm.halt_reason == "too much loss"
    sm.resume()
    assert sm.is_flat()


def test_bars_in_trade_increments_while_in_position():
    sm = StrategyStateMachine()
    sm.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sm.confirm_entry()
    sm.on_bar_close()
    sm.on_bar_close()
    assert sm.position.bars_in_trade == 2


def test_reject_entry_returns_to_flat():
    sm = StrategyStateMachine()
    sm.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sm.reject_entry("execution failed")
    assert sm.is_flat()
    assert sm.position.side == "flat"
