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


def position_region_watchdog(consecutive_unreadable: int,
                             max_before_warn: int = 3) -> Optional[str]:
    """
    Emits a *warning string* (not a halt reason) when the position region has
    been unreadable for `max_before_warn` consecutive polls. The supervisor
    can surface the warning as an event; it is NOT a fail-stop — this region
    is only used for verified fill-price OCR.
    """
    if consecutive_unreadable >= max_before_warn:
        return (f"position_region_unreadable:{consecutive_unreadable} — "
                "verified fill-price will be unavailable until Tradovate is "
                "repositioned or the region is re-calibrated.")
    return None

