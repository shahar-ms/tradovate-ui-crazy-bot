"""
Tests for the position-size + entry-price watcher parsers.
The watcher threads themselves aren't exercised here (mss + Tesseract
dependencies); we test the parsing semantics and supervisor wiring.
"""

from __future__ import annotations

from app.capture.position_watcher import parse_position_size


# ---------------- parse_position_size (signed) ---------------- #


def test_parse_empty_is_flat():
    """An empty crop (broker renders nothing when flat) counts as size=0."""
    assert parse_position_size("") == 0
    assert parse_position_size("   ") == 0
    assert parse_position_size(None) == 0


def test_parse_zero():
    assert parse_position_size("0") == 0


def test_parse_long_is_positive():
    assert parse_position_size("1") == 1
    assert parse_position_size("5") == 5
    assert parse_position_size("12") == 12


def test_parse_short_is_negative():
    """The sign is the side indicator: -1 = short, +1 = long."""
    assert parse_position_size("-1") == -1
    assert parse_position_size("-3") == -3


def test_parse_with_noise():
    """OCR sometimes returns the digit plus extra text."""
    assert parse_position_size("1 ") == 1
    assert parse_position_size("qty 3") == 3
    assert parse_position_size("2,000") == 2000
    assert parse_position_size("qty -2") == -2


def test_parse_garbage_returns_none():
    assert parse_position_size("abc") is None
    assert parse_position_size("xyz!") is None


def test_parse_outlier_returns_none():
    # values wildly out of the plausible contract-count range must be rejected
    assert parse_position_size("99999") is None
    assert parse_position_size("-99999") is None
