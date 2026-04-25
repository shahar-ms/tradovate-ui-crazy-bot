from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.capture.models import HealthState
from app.utils.time_utils import now_ms


RuntimeMode = Literal["CALIBRATION", "PRICE_DEBUG", "PAPER", "ARMED", "HALTED"]

CommandName = Literal[
    "pause", "resume", "halt", "arm", "disarm", "cancel_all", "status", "quit"
]


class RuntimeState(BaseModel):
    mode: RuntimeMode = "PRICE_DEBUG"
    session_id: str = ""
    started_at_ts_ms: int = Field(default_factory=now_ms)
    armed: bool = False
    halted: bool = False
    halt_reason: Optional[str] = None
    # Transient pause (auto-recovers): set when the Tradovate screen isn't visible,
    # price OCR is broken, or anchor drifts. Trading is suspended but the bot keeps
    # polling and resumes automatically when conditions clear.
    paused: bool = False
    pause_reason: Optional[str] = None

    last_price_tick_ts_ms: int = 0
    last_execution_ack_ts_ms: int = 0
    last_price: Optional[float] = None
    # Wall-clock ms of the most recent tick whose price actually CHANGED
    # vs. the prior accepted price. Distinct from last_price_tick_ts_ms,
    # which updates on every accepted tick (including confirming reads of
    # an unchanged price). The "value silence" watchdog gates trading on
    # this so a closed exchange / completely static market can't fire
    # entries even when OCR is happily confirming the same price.
    last_price_change_ts_ms: int = 0

    price_stream_health: HealthState = "ok"
    anchor_guard_ok: bool = True
    strategy_halt_reason: Optional[str] = None

    current_position_side: Literal["flat", "long", "short"] = "flat"
    # Integer position size read from the calibrated position_size_region.
    # None until the watcher has its first successful read. 0 = flat, >0 = open.
    position_size: Optional[int] = None
    # Direction of the most recent raw HUD click (BUY / SELL). The HUD's
    # BUY/SELL buttons fire a direct pyautogui click and bypass the engine,
    # so the engine never learns the side. PositionWatcher uses this to fill
    # in current_position_side when it sees size 0 -> N on an external open.
    last_manual_click_action: Optional[str] = None
    last_intent_action: Optional[str] = None
    last_ack_status: Optional[str] = None
    # verified broker fill (from AckReader OCR of the position region)
    last_fill_price: Optional[float] = None
    last_fill_price_source: Optional[str] = None


class RuntimeCommand(BaseModel):
    command: CommandName
    ts_ms: int = Field(default_factory=now_ms)
    metadata: dict = Field(default_factory=dict)


class ComponentHealth(BaseModel):
    price_stream_health: HealthState = "ok"
    anchor_guard_ok: bool = True
    consecutive_unknown_acks: int = 0
    queue_backlog_price: int = 0
    queue_backlog_intent: int = 0
