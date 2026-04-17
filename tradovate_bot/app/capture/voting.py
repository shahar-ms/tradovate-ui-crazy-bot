"""
Multi-recipe OCR voting.

For each frame we try several preprocess variants, OCR each one, parse and
validate. The voter then picks the best candidate.

Rules:
  - if multiple valid candidates agree on the same price, that price wins
    (confidence = max of the agreeing candidates)
  - if only one valid candidate exists, use it
  - if candidates disagree (multiple distinct prices), reject the frame
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    price: float
    confidence: float
    recipe: str
    raw_text: str


@dataclass
class VoteResult:
    accepted: bool
    price: Optional[float] = None
    confidence: float = 0.0
    recipe: Optional[str] = None
    raw_text: Optional[str] = None
    reason: Optional[str] = None
    agreed_count: int = 0


def vote(candidates: list[Candidate]) -> VoteResult:
    if not candidates:
        return VoteResult(False, reason="no_valid_candidates")

    buckets: dict[float, list[Candidate]] = {}
    for c in candidates:
        buckets.setdefault(round(c.price, 4), []).append(c)

    if len(buckets) == 1:
        price, cs = next(iter(buckets.items()))
        best = max(cs, key=lambda x: x.confidence)
        return VoteResult(
            accepted=True,
            price=price,
            confidence=best.confidence,
            recipe=best.recipe,
            raw_text=best.raw_text,
            agreed_count=len(cs),
        )

    # multiple distinct prices — require a clear majority
    sorted_buckets = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    top_price, top_cs = sorted_buckets[0]
    runner_up = sorted_buckets[1]
    if len(top_cs) > len(runner_up[1]):
        best = max(top_cs, key=lambda x: x.confidence)
        return VoteResult(
            accepted=True,
            price=top_price,
            confidence=best.confidence,
            recipe=best.recipe,
            raw_text=best.raw_text,
            agreed_count=len(top_cs),
        )

    return VoteResult(False, reason="candidates_disagree")
