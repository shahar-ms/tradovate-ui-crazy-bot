from app.capture.parser import parse_price


def test_parses_simple():
    r = parse_price("19234.25")
    assert r.ok
    assert r.value == 19234.25


def test_parses_with_commas():
    r = parse_price("19,234.25")
    assert r.ok
    assert r.value == 19234.25


def test_parses_negative():
    r = parse_price("-12.5")
    assert r.ok
    assert r.value == -12.5


def test_rejects_letters():
    r = parse_price("19a34.25")
    assert not r.ok
    assert "unexpected_chars" in (r.reason or "")


def test_rejects_empty():
    r = parse_price("")
    assert not r.ok


def test_rejects_multiple_decimals():
    r = parse_price("19.23.4")
    assert not r.ok


def test_rejects_trailing_dot():
    r = parse_price("1923.")
    assert not r.ok


def test_rejects_bare_dot():
    r = parse_price(".")
    assert not r.ok


def test_strips_whitespace():
    r = parse_price("  19234.25  ")
    assert r.ok
    assert r.value == 19234.25


def test_rejects_stray_minus():
    r = parse_price("19-234.25")
    assert not r.ok
