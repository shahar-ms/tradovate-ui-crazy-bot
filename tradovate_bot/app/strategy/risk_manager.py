"""
Risk guards for the strategy engine. High-risk opportunity selection is fine,
but process discipline (session caps, cooldowns, consecutive-loss halts) is
always on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from app.models.config import SessionWindow, StrategyConfig

from .models import RiskState

log = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    can_enter: bool
    reason: Optional[str] = None


class RiskManager:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.state = RiskState()

    def on_bar(self) -> None:
        self.state.bars_since_last_exit += 1

    def on_entry(self) -> None:
        self.state.trades_today += 1

    def on_exit(self, realized_pnl_points: Optional[float], now_ms: int) -> None:
        self.state.last_exit_ts_ms = now_ms
        self.state.bars_since_last_exit = 0
        if realized_pnl_points is not None and realized_pnl_points < 0:
            self.state.consecutive_losses += 1
        elif realized_pnl_points is not None:
            self.state.consecutive_losses = 0
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            self.state.halted = True
            self.state.halt_reason = f"max_consecutive_losses:{self.state.consecutive_losses}"

    def reset_daily(self) -> None:
        self.state = RiskState()

    def can_enter(self, now_utc: datetime, price_stream_ok: bool) -> RiskDecision:
        if self.state.halted:
            return RiskDecision(False, self.state.halt_reason or "halted")
        if not price_stream_ok:
            return RiskDecision(False, "price_stream_unhealthy")
        if self.state.trades_today >= self.cfg.max_trades_per_session:
            return RiskDecision(False, f"trade_cap:{self.cfg.max_trades_per_session}")
        if self.state.bars_since_last_exit < self.cfg.cooldown_bars_after_exit:
            return RiskDecision(False, f"cooldown:{self.state.bars_since_last_exit}"
                                         f"<{self.cfg.cooldown_bars_after_exit}")
        if not in_any_session_window(now_utc, self.cfg.session_windows):
            return RiskDecision(False, "outside_session_window")
        return RiskDecision(True)


def in_any_session_window(now_utc: datetime, windows: list[SessionWindow]) -> bool:
    for w in windows:
        if in_session_window(now_utc, w):
            return True
    return False


def in_session_window(now_utc: datetime, window: SessionWindow) -> bool:
    tz = ZoneInfo(window.timezone)
    local = now_utc.astimezone(tz)
    start = time.fromisoformat(window.start)
    end = time.fromisoformat(window.end)
    now_t = local.time()
    # accept range that does not cross midnight
    if start <= end:
        return start <= now_t <= end
    # wrap-around (e.g. 22:00 - 02:00)
    return now_t >= start or now_t <= end
