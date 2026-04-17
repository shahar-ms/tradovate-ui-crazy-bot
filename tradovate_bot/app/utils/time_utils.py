from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ms() -> int:
    return int(time.time() * 1000)


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def session_id() -> str:
    return now_utc().strftime("%Y%m%d_%H%M%S")


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
