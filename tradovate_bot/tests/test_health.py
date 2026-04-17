import time

from app.capture.health import HealthConfig, HealthTracker


def test_health_starts_ok():
    h = HealthTracker()
    assert h.state.health_state == "ok"


def test_health_degrades_then_breaks():
    h = HealthTracker(HealthConfig(degrade_after_consecutive_failures=3,
                                   break_after_consecutive_failures=6,
                                   recover_after_successes=2))
    for _ in range(3):
        h.on_rejection("x")
    assert h.state.health_state == "degraded"
    for _ in range(3):
        h.on_rejection("x")
    assert h.state.health_state == "broken"


def test_health_recovers_after_successes():
    h = HealthTracker(HealthConfig(degrade_after_consecutive_failures=2,
                                   break_after_consecutive_failures=3,
                                   recover_after_successes=2))
    for _ in range(3):
        h.on_rejection("x")
    assert h.state.health_state == "broken"
    h.on_success(19234.25)
    assert h.state.health_state == "degraded"  # still in cooldown
    h.on_success(19234.50)
    assert h.state.health_state == "ok"


def test_health_goes_stale():
    h = HealthTracker(HealthConfig(stale_ms=50))
    h.on_success(1000.0)
    assert h.state.health_state == "ok"
    time.sleep(0.1)
    h.tick_for_staleness()
    assert h.state.stale is True
    assert h.state.health_state == "broken"
