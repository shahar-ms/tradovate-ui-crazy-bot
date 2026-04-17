from app.strategy.levels import LevelDetector, LevelDetectorConfig
from app.strategy.models import MicroBar


def _bar(start: int, o: float, h: float, l: float, c: float) -> MicroBar:
    return MicroBar(start_ts_ms=start, end_ts_ms=start + 1000,
                    open=o, high=h, low=l, close=c)


def test_detects_repeated_resistance():
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=2))
    # build a sequence where bars 2 and 6 are local highs near 100
    highs = [98.0, 99.0, 100.25, 99.5, 99.0, 99.5, 100.0, 99.0, 98.5, 98.0]
    for i, h in enumerate(highs):
        det.on_bar(_bar(i * 1000, o=h - 0.5, h=h, l=h - 1.0, c=h - 0.5))

    rs = det.valid_resistances()
    assert len(rs) == 1
    assert abs(rs[0].price - 100.125) <= 0.5 or abs(rs[0].price - 100.0) <= 0.5
    assert rs[0].touch_count >= 2


def test_detects_repeated_support():
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=2))
    lows = [102.0, 101.0, 100.0, 101.0, 102.0, 101.0, 100.25, 101.0, 102.0, 103.0]
    for i, lo in enumerate(lows):
        det.on_bar(_bar(i * 1000, o=lo + 0.5, h=lo + 1.0, l=lo, c=lo + 0.5))

    ss = det.valid_supports()
    assert len(ss) >= 1
    assert any(abs(s.price - 100.0) <= 0.5 for s in ss)


def test_needs_min_touches():
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=3))
    highs = [98.0, 99.0, 100.0, 99.0, 98.0, 99.0, 100.0, 99.0, 98.0, 97.0]
    for i, h in enumerate(highs):
        det.on_bar(_bar(i * 1000, o=h - 0.5, h=h, l=h - 1.0, c=h - 0.5))

    rs = det.valid_resistances()
    # only 2 distinct swing highs at 100, but min_touches is 3
    assert rs == []


def test_nearest_resistance_above():
    det = LevelDetector(LevelDetectorConfig(lookback_bars=50, swing_k=1,
                                            tolerance_points=0.5, min_touches=2))
    highs = [98.0, 99.0, 100.25, 99.5, 99.0, 99.5, 100.0, 99.0, 98.5, 98.0]
    for i, h in enumerate(highs):
        det.on_bar(_bar(i * 1000, o=h - 0.5, h=h, l=h - 1.0, c=h - 0.5))

    lv = det.nearest_resistance_above(99.0)
    assert lv is not None
