"""
Deterministic level detection over recent bars.

Approach:
  - maintain a rolling window of the last N bars
  - detect local swing highs/lows using a simple fractal: bar i is a swing high
    if its high is strictly greater than the highs of the `swing_k` bars on
    each side. Analogously for swing lows.
  - cluster nearby swings whose prices are within tolerance into a single level
  - a level becomes "valid" once touch_count >= min_touches

Simple, explicit, easy to reason about.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional

from .models import Level, MicroBar


@dataclass
class LevelDetectorConfig:
    lookback_bars: int = 120
    swing_k: int = 2
    tolerance_points: float = 0.5
    min_touches: int = 2


class LevelDetector:
    def __init__(self, cfg: LevelDetectorConfig):
        self.cfg = cfg
        self._bars: deque[MicroBar] = deque(maxlen=cfg.lookback_bars)
        self._resistances: list[Level] = []
        self._supports: list[Level] = []

    def on_bar(self, bar: MicroBar) -> None:
        self._bars.append(bar)
        self._recompute()

    # ---- queries ---- #

    def valid_resistances(self) -> list[Level]:
        return [lv for lv in self._resistances if lv.touch_count >= self.cfg.min_touches]

    def valid_supports(self) -> list[Level]:
        return [lv for lv in self._supports if lv.touch_count >= self.cfg.min_touches]

    def nearest_resistance_above(self, price: float) -> Optional[Level]:
        cands = [lv for lv in self.valid_resistances() if lv.price >= price - self.cfg.tolerance_points]
        if not cands:
            return None
        return min(cands, key=lambda lv: abs(lv.price - price))

    def nearest_support_below(self, price: float) -> Optional[Level]:
        cands = [lv for lv in self.valid_supports() if lv.price <= price + self.cfg.tolerance_points]
        if not cands:
            return None
        return min(cands, key=lambda lv: abs(lv.price - price))

    # ---- internals ---- #

    def _recompute(self) -> None:
        bars = list(self._bars)
        k = self.cfg.swing_k
        if len(bars) < 2 * k + 1:
            return

        swing_highs: list[tuple[int, float]] = []
        swing_lows: list[tuple[int, float]] = []
        for i in range(k, len(bars) - k):
            window = bars[i - k:i + k + 1]
            high = bars[i].high
            low = bars[i].low
            if high == max(b.high for b in window) and sum(1 for b in window if b.high == high) == 1:
                swing_highs.append((bars[i].end_ts_ms, high))
            if low == min(b.low for b in window) and sum(1 for b in window if b.low == low) == 1:
                swing_lows.append((bars[i].end_ts_ms, low))

        self._resistances = _cluster(swing_highs, self.cfg.tolerance_points, kind="resistance")
        self._supports = _cluster(swing_lows, self.cfg.tolerance_points, kind="support")


def _cluster(points: Iterable[tuple[int, float]], tolerance: float, kind: str) -> list[Level]:
    """Cluster (ts, price) points whose prices are within tolerance of each other."""
    sorted_pts = sorted(points, key=lambda p: p[1])
    clusters: list[list[tuple[int, float]]] = []
    for ts, price in sorted_pts:
        if clusters and abs(price - _cluster_mean(clusters[-1])) <= tolerance:
            clusters[-1].append((ts, price))
        else:
            clusters.append([(ts, price)])

    levels: list[Level] = []
    for c in clusters:
        prices = [p for _, p in c]
        tss = [t for t, _ in c]
        levels.append(Level(
            price=round(sum(prices) / len(prices), 4),
            kind=kind,  # type: ignore[arg-type]
            touch_count=len(c),
            first_seen_ts_ms=min(tss),
            last_seen_ts_ms=max(tss),
        ))
    return levels


def _cluster_mean(cluster: list[tuple[int, float]]) -> float:
    return sum(p for _, p in cluster) / len(cluster)
