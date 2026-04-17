from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class PriceTick(BaseModel):
    ts_ms: int
    frame_id: int
    raw_text: str
    price: Optional[float] = None
    confidence: float = Field(0.0, ge=0.0, le=100.0)
    accepted: bool = False
    reject_reason: Optional[str] = None
    source_image_path: Optional[str] = None
    recipe: Optional[str] = None


HealthState = Literal["ok", "degraded", "broken"]


class StreamHealth(BaseModel):
    last_success_ts_ms: int = 0
    last_attempt_ts_ms: int = 0
    consecutive_failures: int = 0
    consecutive_rejections: int = 0
    consecutive_successes: int = 0
    stale: bool = False
    health_state: HealthState = "ok"
    last_accepted_price: Optional[float] = None


class OCRResult(BaseModel):
    raw_text: str
    confidence: float = Field(0.0, ge=0.0, le=100.0)
    engine_name: str = "tesseract"


class PriceReading(BaseModel):
    """Intermediate parse/validate result — not yet a PriceTick."""
    raw_text: str
    parsed: Optional[float] = None
    confidence: float = 0.0
    accepted: bool = False
    reject_reason: Optional[str] = None
    recipe: Optional[str] = None
