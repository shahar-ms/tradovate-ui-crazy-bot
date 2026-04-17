"""
Price validator: turns a parsed float into an accept/reject verdict.

v1 rules:
  1. confidence >= min_confidence
  2. parsed value exists
  3. value aligns to MNQ tick size (0.25)
  4. value is within a plausible absolute range
  5. jump vs previous accepted price is within max_jump_points
     (unless last_accepted is None)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Verdict:
    accepted: bool
    value: Optional[float] = None
    reason: Optional[str] = None


def align_to_tick(price: float, tick_size: float = 0.25, eps: float = 1e-3) -> Optional[float]:
    """Snap to nearest tick if already within eps; else return None."""
    steps = round(price / tick_size)
    normalized = steps * tick_size
    if abs(normalized - price) <= eps:
        return round(normalized, 4)
    return None


class PriceValidator:
    def __init__(
        self,
        min_confidence: float = 70.0,
        tick_size: float = 0.25,
        max_jump_points: float = 30.0,
        min_plausible: float = 1.0,
        max_plausible: float = 1_000_000.0,
    ):
        self.min_confidence = min_confidence
        self.tick_size = tick_size
        self.max_jump_points = max_jump_points
        self.min_plausible = min_plausible
        self.max_plausible = max_plausible

    def check(
        self,
        parsed: Optional[float],
        confidence: float,
        prev_accepted: Optional[float],
    ) -> Verdict:
        if parsed is None:
            return Verdict(False, None, "parse_failed")
        if confidence < self.min_confidence:
            return Verdict(False, None, f"low_confidence:{confidence:.1f}")
        if not (self.min_plausible <= parsed <= self.max_plausible):
            return Verdict(False, None, f"implausible_range:{parsed}")

        snapped = align_to_tick(parsed, self.tick_size)
        if snapped is None:
            return Verdict(False, None, f"not_tick_aligned:{parsed}")

        if prev_accepted is not None:
            jump = abs(snapped - prev_accepted)
            if jump > self.max_jump_points:
                return Verdict(False, None, f"jump_too_large:{jump:.2f}")

        return Verdict(True, snapped, None)
