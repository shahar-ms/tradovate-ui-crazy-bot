from datetime import datetime, timezone

from app.models.config import SessionWindow, StrategyConfig
from app.strategy.risk_manager import RiskManager, in_session_window


def _cfg(**overrides) -> StrategyConfig:
    base = {
        "max_trades_per_session": 3,
        "max_consecutive_losses": 2,
        "cooldown_bars_after_exit": 2,
        "session_windows": [SessionWindow(start="00:00", end="23:59", timezone="UTC")],
    }
    base.update(overrides)
    return StrategyConfig(**base)


def _utc(h: int, m: int) -> datetime:
    return datetime(2026, 1, 1, h, m, tzinfo=timezone.utc)


def test_can_enter_initially():
    r = RiskManager(_cfg())
    # must bump past cooldown first
    for _ in range(3):
        r.on_bar()
    d = r.can_enter(_utc(10, 0), price_stream_ok=True)
    assert d.can_enter, d.reason


def test_blocks_on_unhealthy_stream():
    r = RiskManager(_cfg())
    for _ in range(3):
        r.on_bar()
    d = r.can_enter(_utc(10, 0), price_stream_ok=False)
    assert not d.can_enter
    assert d.reason == "price_stream_unhealthy"


def test_trade_cap_blocks():
    r = RiskManager(_cfg())
    for _ in range(3):
        r.on_bar()
    for _ in range(3):
        r.on_entry()
    d = r.can_enter(_utc(10, 0), True)
    assert not d.can_enter
    assert "trade_cap" in d.reason


def test_consecutive_losses_halts():
    r = RiskManager(_cfg())
    r.on_exit(realized_pnl_points=-5.0, now_ms=1000)
    r.on_exit(realized_pnl_points=-3.0, now_ms=2000)
    assert r.state.halted
    d = r.can_enter(_utc(10, 0), True)
    assert not d.can_enter


def test_winning_trade_resets_consecutive_losses():
    r = RiskManager(_cfg())
    r.on_exit(realized_pnl_points=-5.0, now_ms=1000)
    r.on_exit(realized_pnl_points=2.0, now_ms=2000)  # win
    assert not r.state.halted
    assert r.state.consecutive_losses == 0


def test_cooldown_blocks_immediately_after_exit():
    r = RiskManager(_cfg())
    for _ in range(3):
        r.on_bar()
    r.on_exit(realized_pnl_points=1.0, now_ms=5000)
    d = r.can_enter(_utc(10, 0), True)
    assert not d.can_enter
    assert "cooldown" in d.reason


def test_session_window_wrap_around():
    w = SessionWindow(start="22:00", end="02:00", timezone="UTC")
    assert in_session_window(_utc(23, 30), w)
    assert in_session_window(_utc(1, 30), w)
    assert not in_session_window(_utc(12, 0), w)


def test_session_window_normal():
    w = SessionWindow(start="16:30", end="18:30", timezone="UTC")
    assert in_session_window(_utc(17, 0), w)
    assert not in_session_window(_utc(14, 0), w)
