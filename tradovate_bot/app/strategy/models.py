from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.utils.time_utils import now_ms


SideT = Literal["flat", "long", "short"]
LevelKindT = Literal["resistance", "support"]
SignalActionT = Literal["BUY", "SELL", "CANCEL_ALL", "EXIT_LONG", "EXIT_SHORT"]
StrategyStateT = Literal["FLAT", "PENDING_ENTRY", "LONG", "SHORT", "PENDING_EXIT", "HALTED"]


def _new_intent_id() -> str:
    return uuid.uuid4().hex[:12]


class MicroBar(BaseModel):
    start_ts_ms: int
    end_ts_ms: int
    open: float
    high: float
    low: float
    close: float

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2


class Level(BaseModel):
    price: float
    kind: LevelKindT
    touch_count: int = 1
    first_seen_ts_ms: int
    last_seen_ts_ms: int


class SignalIntent(BaseModel):
    intent_id: str = Field(default_factory=_new_intent_id)
    action: SignalActionT
    reason: str = ""
    trigger_price: Optional[float] = None
    ts_ms: int = Field(default_factory=now_ms)
    metadata: dict = Field(default_factory=dict)


class PositionState(BaseModel):
    side: SideT = "flat"
    entry_price: Optional[float] = None
    entry_ts_ms: Optional[int] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    bars_in_trade: int = 0


class RiskState(BaseModel):
    trades_today: int = 0
    consecutive_losses: int = 0
    last_exit_ts_ms: Optional[int] = None
    bars_since_last_exit: int = 10_000
    halted: bool = False
    halt_reason: Optional[str] = None
