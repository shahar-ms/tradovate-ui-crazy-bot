"""
Price validator: turns a parsed float into an accept/reject verdict.

Rules:
  1. parsed value exists
  2. value is within a plausible absolute range
  3. value aligns to MNQ tick size (0.25)
  4. confidence floor:
       - novel value vs. the last accepted: full `min_confidence`
       - SAME value as the last accepted:  soft floor (`min_confidence * 0.7`)
     The soft floor exists because static markets (and Tradovate's price
     cell during quiet periods) produce slightly lower per-frame OCR
     confidence due to subpixel anti-aliasing changes — but if the parsed
     value keeps matching what we already trust, the cross-frame agreement
     is strong evidence on its own. Without this, the bot logs hundreds of
     `low_confidence` rejections and flips health to `broken` on a
     completely static screen.
  5. jump vs. previous accepted price is within `max_jump_points`
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

    # Multiplier applied to min_confidence when the parsed price matches
    # the last accepted value. 0.7 means a 70-floor becomes a 49-floor for
    # confirming reads. Picked empirically: low enough to keep static
    # markets from flipping to "broken", high enough that pure OCR noise
    # still gets rejected.
    SAME_VALUE_CONFIDENCE_RATIO: float = 0.7

    def check(
        self,
        parsed: Optional[float],
        confidence: float,
        prev_accepted: Optional[float],
    ) -> Verdict:
        if parsed is None:
            return Verdict(False, None, "parse_failed")
        if not (self.min_plausible <= parsed <= self.max_plausible):
            return Verdict(False, None, f"implausible_range:{parsed}")

        snapped = align_to_tick(parsed, self.tick_size)
        if snapped is None:
            return Verdict(False, None, f"not_tick_aligned:{parsed}")

        # Adaptive confidence floor: if the parsed (tick-snapped) value
        # equals the last accepted price, a low-confidence read is still
        # trustworthy because we're confirming, not introducing.
        is_confirming = (
            prev_accepted is not None
            and abs(snapped - prev_accepted) < 1e-6
        )
        floor = (self.min_confidence * self.SAME_VALUE_CONFIDENCE_RATIO
                 if is_confirming else self.min_confidence)
        if confidence < floor:
            return Verdict(False, None, f"low_confidence:{confidence:.1f}")

        if prev_accepted is not None:
            jump = abs(snapped - prev_accepted)
            if jump > self.max_jump_points:
                return Verdict(False, None, f"jump_too_large:{jump:.2f}")

        return Verdict(True, snapped, None)
