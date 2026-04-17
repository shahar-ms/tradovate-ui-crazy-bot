"""
Micro-bar builder: turns a tick stream into OHLC bars of configured seconds.

Rules:
  - first tick starts the current bar
  - later ticks update high/low/close
  - bar closes when a tick arrives in a later bar interval
  - missing intervals do not invent synthetic bars
"""

from __future__ import annotations

from typing import Optional

from .models import MicroBar


def _interval_start(ts_ms: int, bar_seconds: int) -> int:
    bar_ms = bar_seconds * 1000
    return (ts_ms // bar_ms) * bar_ms


class BarBuilder:
    def __init__(self, bar_seconds: int = 1):
        if bar_seconds <= 0:
            raise ValueError("bar_seconds must be > 0")
        self.bar_seconds = bar_seconds
        self._current_start_ms: Optional[int] = None
        self._open: float = 0.0
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._has_data = False

    def on_tick(self, ts_ms: int, price: float) -> Optional[MicroBar]:
        """
        Update internal state with a new tick. Returns a closed bar if this
        tick belongs to a later bar interval than the current one.
        """
        start = _interval_start(ts_ms, self.bar_seconds)
        if self._current_start_ms is None:
            self._start_new(start, price)
            return None

        if start == self._current_start_ms:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            return None

        if start > self._current_start_ms:
            closed = self._snapshot_current(end_ms=self._current_start_ms + self.bar_seconds * 1000)
            self._start_new(start, price)
            return closed

        # out-of-order tick: ignore
        return None

    def force_close(self, now_ms: int) -> Optional[MicroBar]:
        if not self._has_data or self._current_start_ms is None:
            return None
        end = max(now_ms, self._current_start_ms + self.bar_seconds * 1000)
        snap = self._snapshot_current(end_ms=end)
        self._reset()
        return snap

    def _start_new(self, start_ms: int, price: float) -> None:
        self._current_start_ms = start_ms
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._has_data = True

    def _snapshot_current(self, end_ms: int) -> MicroBar:
        return MicroBar(
            start_ts_ms=self._current_start_ms or 0,
            end_ts_ms=end_ms,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
        )

    def _reset(self) -> None:
        self._current_start_ms = None
        self._has_data = False
