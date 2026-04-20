"""
Tests for the new position-size OCR watcher + its integer parser.
The watcher thread itself isn't exercised here (mss + Tesseract dependencies);
we test the parsing semantics and supervisor wiring.
"""

from __future__ import annotations

from app.capture.position_watcher import parse_position_size


def test_parse_empty_is_flat():
    """An empty crop (broker renders nothing when flat) counts as size=0."""
    assert parse_position_size("") == 0
    assert parse_position_size("   ") == 0
    assert parse_position_size(None) == 0


def test_parse_zero():
    assert parse_position_size("0") == 0


def test_parse_simple_int():
    assert parse_position_size("1") == 1
    assert parse_position_size("5") == 5
    assert parse_position_size("12") == 12


def test_parse_with_noise():
    """OCR sometimes returns the digit plus extra text."""
    assert parse_position_size("1 ") == 1
    assert parse_position_size("qty 3") == 3
    assert parse_position_size("2,000") == 2000


def test_parse_garbage_returns_none():
    assert parse_position_size("abc") is None
    assert parse_position_size("xyz!") is None


def test_parse_ignores_minor_negatives():
    """OCR noise sometimes emits a spurious minus sign — treat as absolute."""
    assert parse_position_size("-1") == 1


def test_parse_outlier_returns_none():
    # values wildly out of the plausible contract-count range must be rejected
    assert parse_position_size("99999") is None
    assert parse_position_size("-9999") is None
