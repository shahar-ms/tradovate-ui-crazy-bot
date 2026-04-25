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
    # How long the parsed price can stay UNCHANGED before we treat the
    # market as inactive (e.g. exchange closed, weekend, holiday). 60s
    # is generous: liquid MNQ ticks every few seconds in regular hours,
    # and a quiet 60s window on a closed market is unambiguous. Pause —
    # not halt — because trading auto-resumes the moment a fresh tick
    # with a different price arrives.
    max_value_silence_ms: int = 60_000
    max_consecutive_unknown_acks: int = 2
    max_queue_backlog: int = 512


def price_watchdog(health_state: HealthState, ms_since_last_tick: int,
                   cfg: WatchdogConfig) -> Optional[str]:
    if health_state == "broken":
        return "price_stream_broken"
    if ms_since_last_tick > cfg.max_price_silence_ms:
        return f"price_silence:{ms_since_last_tick}ms"
    return None


def value_silence_watchdog(ms_since_last_change: int,
                           cfg: WatchdogConfig) -> Optional[str]:
    """Pause when the last_price hasn't actually moved for too long —
    typically: market closed, exchange holiday, low-liquidity dead zone.
    Distinct from `price_watchdog`: there OCR ticks may still be flowing
    (confirming reads), but the underlying market isn't active.

    Returns None when ms_since_last_change is 0 (no value change recorded
    yet — fresh boot) so the bot doesn't pause itself the instant it starts."""
    if ms_since_last_change <= 0:
        return None
    if ms_since_last_change > cfg.max_value_silence_ms:
        return f"market_inactive:{ms_since_last_change // 1000}s_no_price_change"
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

