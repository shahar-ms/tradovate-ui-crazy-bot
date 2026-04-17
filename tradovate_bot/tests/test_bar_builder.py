from app.strategy.bar_builder import BarBuilder


def test_first_tick_no_bar():
    b = BarBuilder(bar_seconds=1)
    assert b.on_tick(1000, 100.0) is None


def test_same_interval_updates_ohlc():
    b = BarBuilder(bar_seconds=1)
    b.on_tick(1000, 100.0)
    b.on_tick(1200, 101.0)
    bar = b.on_tick(1500, 99.5)
    assert bar is None  # still same 1-s bucket [1000, 2000)


def test_closes_on_next_interval():
    b = BarBuilder(bar_seconds=1)
    assert b.on_tick(1000, 100.0) is None
    assert b.on_tick(1500, 105.0) is None
    closed = b.on_tick(2050, 102.0)
    assert closed is not None
    assert closed.open == 100.0
    assert closed.high == 105.0
    assert closed.low == 100.0
    assert closed.close == 105.0
    assert closed.start_ts_ms == 1000
    assert closed.end_ts_ms == 2000


def test_force_close_returns_current():
    b = BarBuilder(bar_seconds=1)
    b.on_tick(1000, 100.0)
    b.on_tick(1500, 105.0)
    closed = b.force_close(now_ms=1700)
    assert closed is not None
    assert closed.close == 105.0
    # second force_close with no data should return None
    assert b.force_close(now_ms=1700) is None


def test_ignores_out_of_order_tick():
    b = BarBuilder(bar_seconds=1)
    b.on_tick(2000, 100.0)
    assert b.on_tick(1000, 50.0) is None  # ignored
