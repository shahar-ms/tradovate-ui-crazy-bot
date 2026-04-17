from app.strategy.levels import LevelDetector, LevelDetectorConfig
from app.strategy.models import MicroBar
from app.strategy.signal_rules import SweepConfig, SweepSignalEngine


def _bar(start: int, o: float, h: float, l: float, c: float) -> MicroBar:
    return MicroBar(start_ts_ms=start, end_ts_ms=start + 1000,
                    open=o, high=h, low=l, close=c)


def _detector_with_resistance_at(price: float) -> LevelDetector:
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=2))
    highs = [price - 2, price - 1, price, price - 1, price - 2, price - 1,
             price, price - 1, price - 2, price - 3]
    for i, h in enumerate(highs):
        det.on_bar(_bar(i * 1000, o=h - 0.5, h=h, l=h - 1.0, c=h - 0.5))
    return det


def _detector_with_support_at(price: float) -> LevelDetector:
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=2))
    lows = [price + 2, price + 1, price, price + 1, price + 2, price + 1,
            price, price + 1, price + 2, price + 3]
    for i, lo in enumerate(lows):
        det.on_bar(_bar(i * 1000, o=lo + 0.5, h=lo + 1.0, l=lo, c=lo + 0.5))
    return det


def test_short_entry_on_resistance_sweep_reversal():
    det = _detector_with_resistance_at(100.0)
    rules = SweepSignalEngine(SweepConfig(break_distance_points=1.0, return_timeout_bars=5))

    # bar that pierces 100 by 1.0+
    breakout = _bar(20_000, o=100.0, h=101.5, l=99.5, c=101.0)
    sig1 = rules.on_bar(breakout, det)
    assert sig1 is None  # no failed close yet

    # next bar closes back below 100
    fail = _bar(21_000, o=101.0, h=101.2, l=99.0, c=99.5)
    sig2 = rules.on_bar(fail, det)
    assert sig2 is not None
    assert sig2.action == "SELL"
    assert sig2.level.kind == "resistance"


def test_long_entry_on_support_sweep_reversal():
    det = _detector_with_support_at(100.0)
    rules = SweepSignalEngine(SweepConfig(break_distance_points=1.0, return_timeout_bars=5))

    breakdown = _bar(20_000, o=100.0, h=100.5, l=98.5, c=99.0)
    assert rules.on_bar(breakdown, det) is None

    recover = _bar(21_000, o=99.0, h=101.0, l=98.8, c=100.5)
    sig = rules.on_bar(recover, det)
    assert sig is not None
    assert sig.action == "BUY"
    assert sig.level.kind == "support"


def test_timeout_expires_candidate():
    det = _detector_with_resistance_at(100.0)
    rules = SweepSignalEngine(SweepConfig(break_distance_points=1.0, return_timeout_bars=2))

    rules.on_bar(_bar(20_000, 100.0, 101.5, 99.5, 101.0), det)
    # 3 flat bars -- beyond timeout
    rules.on_bar(_bar(21_000, 101.0, 101.2, 100.5, 100.8), det)
    rules.on_bar(_bar(22_000, 100.8, 101.0, 100.5, 100.7), det)
    rules.on_bar(_bar(23_000, 100.7, 100.9, 100.3, 100.5), det)

    # now a close below 100 should NOT fire because candidate expired
    sig = rules.on_bar(_bar(24_000, 100.5, 100.6, 99.0, 99.5), det)
    assert sig is None


def test_no_signal_without_valid_level():
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=5))
    rules = SweepSignalEngine(SweepConfig(break_distance_points=1.0, return_timeout_bars=5))
    # a single swing high cannot become a valid level with min_touches=5
    det.on_bar(_bar(0, 99.0, 100.25, 98.5, 99.5))
    bar = _bar(1_000, 99.5, 101.5, 99.0, 99.2)
    assert rules.on_bar(bar, det) is None
