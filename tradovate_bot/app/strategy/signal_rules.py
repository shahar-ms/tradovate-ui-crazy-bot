"""
Entry rule: liquidity sweep / failed breakout around recent levels.

Short pattern:
  1. there is a valid resistance R
  2. a bar's high pierced above R by >= sweep_break_distance_points
  3. within sweep_return_timeout_bars, a later bar closes back below R
  4. -> emit SELL

Long pattern: symmetric around a support S.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .levels import LevelDetector
from .models import Level, MicroBar, SignalActionT


@dataclass
class SweepConfig:
    break_distance_points: float = 1.0
    return_timeout_bars: int = 5


@dataclass
class SweepCandidate:
    level: Level
    broke_at_bar_index: int
    break_extreme: float


@dataclass
class EntrySignal:
    action: SignalActionT
    level: Level
    trigger_price: float
    reason: str


class SweepSignalEngine:
    """
    Tracks pending "sweep in progress" candidates per side, and emits an entry
    signal when a subsequent bar closes back through the level in time.
    """

    def __init__(self, cfg: SweepConfig):
        self.cfg = cfg
        self._pending_short: Optional[SweepCandidate] = None  # resistance broken up
        self._pending_long: Optional[SweepCandidate] = None   # support broken down
        self._bar_index = 0

    def reset(self) -> None:
        self._pending_short = None
        self._pending_long = None

    def on_bar(self, bar: MicroBar, detector: LevelDetector) -> Optional[EntrySignal]:
        self._bar_index += 1

        # expire pending candidates that outlive the timeout
        if self._pending_short and self._bar_index - self._pending_short.broke_at_bar_index > self.cfg.return_timeout_bars:
            self._pending_short = None
        if self._pending_long and self._bar_index - self._pending_long.broke_at_bar_index > self.cfg.return_timeout_bars:
            self._pending_long = None

        # --- short side: detect break above a resistance, then failed close ---
        for lv in detector.valid_resistances():
            if bar.high >= lv.price + self.cfg.break_distance_points:
                # refresh/start candidate
                if self._pending_short is None or lv.price != self._pending_short.level.price:
                    self._pending_short = SweepCandidate(
                        level=lv, broke_at_bar_index=self._bar_index, break_extreme=bar.high
                    )
                else:
                    self._pending_short.break_extreme = max(self._pending_short.break_extreme, bar.high)

        if self._pending_short and bar.close < self._pending_short.level.price:
            sig = EntrySignal(
                action="SELL",
                level=self._pending_short.level,
                trigger_price=bar.close,
                reason="resistance_sweep_reversal",
            )
            self._pending_short = None
            return sig

        # --- long side: symmetric ---
        for lv in detector.valid_supports():
            if bar.low <= lv.price - self.cfg.break_distance_points:
                if self._pending_long is None or lv.price != self._pending_long.level.price:
                    self._pending_long = SweepCandidate(
                        level=lv, broke_at_bar_index=self._bar_index, break_extreme=bar.low
                    )
                else:
                    self._pending_long.break_extreme = min(self._pending_long.break_extreme, bar.low)

        if self._pending_long and bar.close > self._pending_long.level.price:
            sig = EntrySignal(
                action="BUY",
                level=self._pending_long.level,
                trigger_price=bar.close,
                reason="support_sweep_reversal",
            )
            self._pending_long = None
            return sig

        return None
