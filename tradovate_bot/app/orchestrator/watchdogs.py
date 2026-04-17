"""
Watchdogs: return a halt reason (string) if conditions require halting, else None.
Pure functions over snapshots — easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.capture.models import HealthState

from .runtime_models import ComponentHealth


@dataclass
class WatchdogConfig:
    max_price_silence_ms: int = 3000
    max_consecutive_unknown_acks: int = 2
    max_queue_backlog: int = 512


def price_watchdog(health_state: HealthState, ms_since_last_tick: int,
                   cfg: WatchdogConfig) -> Optional[str]:
    if health_state == "broken":
        return "price_stream_broken"
    if ms_since_last_tick > cfg.max_price_silence_ms:
        return f"price_silence:{ms_since_last_tick}ms"
    return None


def anchor_watchdog(anchor_ok: bool) -> Optional[str]:
    return None if anchor_ok else "anchor_drift"


def execution_watchdog(consecutive_unknown: int, cfg: WatchdogConfig) -> Optional[str]:
    if consecutive_unknown >= cfg.max_consecutive_unknown_acks:
        return f"unknown_ack_streak:{consecutive_unknown}"
    return None


def queue_watchdog(backlog: dict[str, int], cfg: WatchdogConfig) -> Optional[str]:
    for name, size in backlog.items():
        if size >= cfg.max_queue_backlog:
            return f"queue_backlog:{name}={size}"
    return None


def first_halt_reason(reasons: list[Optional[str]]) -> Optional[str]:
    for r in reasons:
        if r:
            return r
    return None
