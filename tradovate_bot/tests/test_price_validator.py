from app.capture.validator import PriceValidator, align_to_tick


def test_align_to_tick_accepts_quarters():
    assert align_to_tick(19234.25) == 19234.25
    assert align_to_tick(19234.50) == 19234.50
    assert align_to_tick(19234.75) == 19234.75
    assert align_to_tick(19234.00) == 19234.00


def test_align_to_tick_rejects_off_tick():
    assert align_to_tick(19234.17) is None
    assert align_to_tick(19234.10) is None


def test_validator_accepts_aligned():
    v = PriceValidator(min_confidence=50.0)
    ver = v.check(19234.25, confidence=85.0, prev_accepted=None)
    assert ver.accepted
    assert ver.value == 19234.25


def test_validator_rejects_off_tick():
    v = PriceValidator(min_confidence=50.0)
    ver = v.check(19234.17, confidence=85.0, prev_accepted=None)
    assert not ver.accepted
    assert "not_tick_aligned" in (ver.reason or "")


def test_validator_rejects_low_confidence():
    v = PriceValidator(min_confidence=70.0)
    ver = v.check(19234.25, confidence=40.0, prev_accepted=None)
    assert not ver.accepted
    assert "low_confidence" in (ver.reason or "")


def test_validator_rejects_large_jump():
    v = PriceValidator(min_confidence=50.0, max_jump_points=30.0)
    ver = v.check(19300.00, confidence=90.0, prev_accepted=19200.00)
    assert not ver.accepted
    assert "jump_too_large" in (ver.reason or "")


def test_validator_accepts_reasonable_jump():
    v = PriceValidator(min_confidence=50.0, max_jump_points=30.0)
    ver = v.check(19210.00, confidence=90.0, prev_accepted=19200.00)
    assert ver.accepted


def test_validator_rejects_implausible():
    v = PriceValidator(min_confidence=50.0, min_plausible=100.0, max_plausible=100000.0)
    ver = v.check(0.25, confidence=95.0, prev_accepted=None)
    assert not ver.accepted


def test_validator_rejects_missing_parse():
    v = PriceValidator(min_confidence=50.0)
    ver = v.check(None, confidence=95.0, prev_accepted=None)
    assert not ver.accepted
    assert ver.reason == "parse_failed"
