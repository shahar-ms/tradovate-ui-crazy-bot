from app.orchestrator.watchdogs import (WatchdogConfig, anchor_watchdog,
                                        execution_watchdog, first_halt_reason,
                                        price_watchdog, queue_watchdog,
                                        value_silence_watchdog)


def test_price_watchdog_broken_stream():
    assert price_watchdog("broken", 100, WatchdogConfig()) == "price_stream_broken"


def test_price_watchdog_silence():
    cfg = WatchdogConfig(max_price_silence_ms=1000)
    r = price_watchdog("ok", 2000, cfg)
    assert r and r.startswith("price_silence")


def test_price_watchdog_ok():
    assert price_watchdog("ok", 100, WatchdogConfig()) is None
    assert price_watchdog("degraded", 100, WatchdogConfig()) is None


def test_anchor_watchdog():
    assert anchor_watchdog(True) is None
    assert anchor_watchdog(False) == "anchor_drift"


def test_execution_watchdog():
    cfg = WatchdogConfig(max_consecutive_unknown_acks=2)
    assert execution_watchdog(0, cfg) is None
    assert execution_watchdog(1, cfg) is None
    r = execution_watchdog(2, cfg)
    assert r and r.startswith("unknown_ack_streak")


def test_queue_watchdog():
    cfg = WatchdogConfig(max_queue_backlog=100)
    assert queue_watchdog({"price": 10, "intent": 0}, cfg) is None
    r = queue_watchdog({"price": 10, "intent": 200}, cfg)
    assert r and r.startswith("queue_backlog:intent")


def test_first_halt_reason_picks_first_non_none():
    assert first_halt_reason([None, None, "x", "y"]) == "x"
    assert first_halt_reason([None, None]) is None


# ----- value_silence_watchdog ----- #


def test_value_silence_watchdog_quiet_when_within_threshold():
    cfg = WatchdogConfig(max_value_silence_ms=60_000)
    assert value_silence_watchdog(30_000, cfg) is None


def test_value_silence_watchdog_pauses_when_exceeded():
    cfg = WatchdogConfig(max_value_silence_ms=60_000)
    r = value_silence_watchdog(120_000, cfg)
    assert r and r.startswith("market_inactive")
    assert "120s" in r


def test_value_silence_watchdog_silent_at_zero():
    """Bot just booted, no value-change recorded yet — must NOT pause."""
    cfg = WatchdogConfig(max_value_silence_ms=60_000)
    assert value_silence_watchdog(0, cfg) is None
    assert value_silence_watchdog(-5, cfg) is None
