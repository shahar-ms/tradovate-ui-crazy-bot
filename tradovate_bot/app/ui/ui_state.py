"""
Small pushed-updates state store for the UI.

Workers push via Qt signals; UiState caches the latest values. Pages read
from UiState on paint — they never block waiting on workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UiState:
    # runtime
    mode: str = "DISCONNECTED"
    session_id: str = ""
    armed: bool = False
    halted: bool = False
    halt_reason: Optional[str] = None
    uptime_seconds: int = 0

    # market
    last_price: Optional[float] = None
    last_price_ts_ms: int = 0
    last_confidence: float = 0.0
    price_stream_health: str = "inactive"
    accepted_tick_count: int = 0
    rejected_tick_count: int = 0
    last_reject_reason: Optional[str] = None

    # strategy
    position_side: str = "flat"
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    last_intent_action: Optional[str] = None
    last_intent_reason: Optional[str] = None

    # execution
    last_ack_status: Optional[str] = None
    last_ack_message: Optional[str] = None
    last_ack_ts_ms: int = 0
    consecutive_unknown_acks: int = 0

    # guards / calibration
    anchor_ok: bool = True
    anchor_similarity: float = 0.0
    calibration_loaded: bool = False
    monitor_index: int = 1
    screen_size: tuple[int, int] = (0, 0)

    # ring buffer of recent events for the dashboard
    recent_events: list[dict] = field(default_factory=list)

    RECENT_EVENTS_MAX: int = 200

    def push_event(self, event: dict) -> None:
        self.recent_events.append(event)
        if len(self.recent_events) > self.RECENT_EVENTS_MAX:
            # keep the tail
            self.recent_events = self.recent_events[-self.RECENT_EVENTS_MAX:]
