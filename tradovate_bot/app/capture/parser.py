"""
Turn raw OCR text into either a float price or a parse failure. Conservative:
prefer to reject ambiguous output rather than invent a price.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


NUMBER_RE = re.compile(r"^-?\d{1,7}(?:\.\d{1,4})?$")


@dataclass
class ParseResult:
    value: Optional[float]
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def parse_price(raw_text: str) -> ParseResult:
    if raw_text is None:
        return ParseResult(None, "empty_text")

    text = raw_text.strip()
    if not text:
        return ParseResult(None, "empty_text")

    # strip thousand separators
    cleaned = text.replace(",", "").replace(" ", "")

    # reject if any unexpected character remains
    if re.search(r"[^0-9.\-]", cleaned):
        return ParseResult(None, f"unexpected_chars:{cleaned!r}")

    # reject multiple decimals
    if cleaned.count(".") > 1:
        return ParseResult(None, f"multiple_decimals:{cleaned!r}")

    # minus only allowed as a leading sign
    if "-" in cleaned and not cleaned.startswith("-"):
        return ParseResult(None, f"stray_minus:{cleaned!r}")
    if cleaned.count("-") > 1:
        return ParseResult(None, f"multiple_minuses:{cleaned!r}")

    # require the entire cleaned string to be a valid number (no partial matches)
    if not NUMBER_RE.match(cleaned):
        return ParseResult(None, f"malformed_number:{cleaned!r}")

    try:
        value = float(cleaned)
    except ValueError:
        return ParseResult(None, f"float_cast_failed:{cleaned!r}")

    return ParseResult(value=value, reason=None)
