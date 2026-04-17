from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.utils.time_utils import now_ms

from .models import HealthState, StreamHealth


@dataclass
class HealthConfig:
    degrade_after_consecutive_failures: int = 5
    break_after_consecutive_failures: int = 20
    recover_after_successes: int = 3
    stale_ms: int = 1500


class HealthTracker:
    """Tracks accepted/rejected frames and exposes an ok/degraded/broken state."""

    def __init__(self, cfg: Optional[HealthConfig] = None):
        self.cfg = cfg or HealthConfig()
        self.state = StreamHealth()

    def on_success(self, price: float) -> None:
        t = now_ms()
        self.state.last_success_ts_ms = t
        self.state.last_attempt_ts_ms = t
        self.state.consecutive_failures = 0
        self.state.consecutive_rejections = 0
        self.state.consecutive_successes += 1
        self.state.last_accepted_price = price
        self.state.stale = False
        self._recompute_state()

    def on_rejection(self, reason: str) -> None:  # noqa: ARG002
        t = now_ms()
        self.state.last_attempt_ts_ms = t
        self.state.consecutive_rejections += 1
        self.state.consecutive_failures += 1
        self.state.consecutive_successes = 0
        self._recompute_state()

    def on_failure(self) -> None:
        t = now_ms()
        self.state.last_attempt_ts_ms = t
        self.state.consecutive_failures += 1
        self.state.consecutive_successes = 0
        self._recompute_state()

    def tick_for_staleness(self) -> None:
        if self.state.last_success_ts_ms == 0:
            return
        age = now_ms() - self.state.last_success_ts_ms
        self.state.stale = age > self.cfg.stale_ms
        if self.state.stale:
            self._recompute_state()

    def _recompute_state(self) -> None:
        cf = self.state.consecutive_failures
        cs = self.state.consecutive_successes
        prev: HealthState = self.state.health_state

        if cf >= self.cfg.break_after_consecutive_failures or self.state.stale:
            new: HealthState = "broken"
        elif cf >= self.cfg.degrade_after_consecutive_failures:
            new = "degraded"
        else:
            new = "ok"

        # require a cooldown of successes before recovering from broken
        if prev == "broken" and new != "broken" and cs < self.cfg.recover_after_successes:
            new = "degraded"

        self.state.health_state = new

    def snapshot(self) -> StreamHealth:
        return self.state.model_copy()
