"""
Acknowledgement reader. Tries to detect "something changed after my click"
using whatever evidence is available (status / position region diff).

For BUY / SELL: when a position_region is calibrated, OCR it repeatedly for
up to 1.2s and accept the first parsed price that differs from the pre-click
text AND meets the confidence threshold. The parsed price is the verified
broker fill — much better than pre-click OCR for PnL.

For CANCEL_ALL: keep the existing pixel-diff approach (there is no fill
price on a cancel). Engine clears position state on status=ok regardless.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.capture.ocr_reader import OCRReader, build_reader
from app.capture.parser import parse_price
from app.capture.screen_capture import ScreenCapture
from app.models.common import Region, ScreenMap
from app.utils import image_utils as iu

log = logging.getLogger(__name__)


@dataclass
class AckSignal:
    status: str                                       # "ok" | "failed" | "unknown"
    message: str = ""
    evidence_image: Optional[np.ndarray] = None
    fill_price: Optional[float] = None
    fill_price_confidence: Optional[float] = None
    fill_price_source: Optional[str] = None           # position_ocr | stale | timeout | unavailable


class AckReader:
    """
    Captures a "before" snapshot of a chosen evidence region (if any), then
    polls the region after the click. For BUY / SELL, parses the fill price
    via OCR and returns it. For CANCEL_ALL, falls back to pixel diff.
    """

    # fill-price polling parameters
    POLL_INTERVAL_MS = 150
    MAX_POLL_MS = 1200

    def __init__(
        self,
        screen_map: ScreenMap,
        capture: Optional[ScreenCapture] = None,
        ocr_reader: Optional[OCRReader] = None,
        change_threshold: float = 0.02,
        min_ocr_confidence: float = 60.0,
    ):
        self.screen_map = screen_map
        self._external_capture = capture is not None
        self._capture = capture or ScreenCapture(screen_map.monitor_index)
        self._ocr_reader: Optional[OCRReader] = ocr_reader
        self._ocr_lazy_attempted = False
        self.change_threshold = change_threshold
        self.min_ocr_confidence = min_ocr_confidence

    # ---- public API ---- #

    def capture_before(self, action: str) -> Optional[np.ndarray]:
        region = self._evidence_region(action)
        if region is None:
            return None
        try:
            return self._capture.grab_region(region)
        except Exception as e:
            log.warning("ack: capture_before failed: %s", e)
            return None

    def read_after(self, action: str, before: Optional[np.ndarray]) -> AckSignal:
        region = self._evidence_region(action)
        if region is None or before is None:
            # no evidence region — can't judge
            time.sleep(self.POLL_INTERVAL_MS / 1000.0)
            return AckSignal(status="unknown", message="no_evidence_region",
                             fill_price_source="unavailable")

        if action == "CANCEL_ALL":
            return self._read_cancel_ack(region, before)
        # BUY / SELL: try to OCR the fill price
        return self._read_fill_ack(region, before)

    def close(self) -> None:
        if not self._external_capture:
            self._capture.close()

    # ---- internals ---- #

    def _evidence_region(self, action: str) -> Optional[Region]:
        if action == "CANCEL_ALL":
            return self.screen_map.status_region or self.screen_map.position_region
        return self.screen_map.position_region or self.screen_map.status_region

    def _read_cancel_ack(self, region: Region, before: np.ndarray) -> AckSignal:
        # give Tradovate a moment before diffing
        time.sleep(self.POLL_INTERVAL_MS / 1000.0)
        try:
            after = self._capture.grab_region(region)
        except Exception as e:
            return AckSignal(status="unknown", message=f"capture_after_failed:{e}",
                             fill_price_source="unavailable")
        sim = iu.similarity_score(before, after)
        delta = 1.0 - sim
        if delta >= self.change_threshold:
            return AckSignal(status="ok", message=f"delta={delta:.3f}",
                             evidence_image=after, fill_price_source="unavailable")
        return AckSignal(status="unknown", message=f"no_visible_change:delta={delta:.3f}",
                         evidence_image=after, fill_price_source="unavailable")

    def _read_fill_ack(self, region: Region, before: np.ndarray) -> AckSignal:
        """Poll the region until a new, parseable price appears; parse it as the fill."""
        reader = self._get_ocr_reader()
        before_text = self._ocr_text(reader, before) if reader is not None else ""

        poll_interval_s = self.POLL_INTERVAL_MS / 1000.0
        max_iters = max(1, self.MAX_POLL_MS // self.POLL_INTERVAL_MS)
        after: Optional[np.ndarray] = None

        for _ in range(max_iters):
            time.sleep(poll_interval_s)
            try:
                after = self._capture.grab_region(region)
            except Exception as e:
                return AckSignal(status="unknown",
                                 message=f"capture_after_failed:{e}",
                                 fill_price_source="unavailable")
            if reader is None:
                # no OCR available — fall back to pixel diff
                sim = iu.similarity_score(before, after)
                if 1.0 - sim >= self.change_threshold:
                    return AckSignal(status="ok",
                                     message=f"delta={1.0 - sim:.3f}",
                                     evidence_image=after,
                                     fill_price_source="unavailable")
                continue

            ocr = reader.read(after)
            text = (ocr.raw_text or "").strip()
            parsed = parse_price(text)
            if (
                parsed.ok
                and text != before_text
                and ocr.confidence >= self.min_ocr_confidence
            ):
                return AckSignal(
                    status="ok",
                    message=f"filled_at={parsed.value}",
                    evidence_image=after,
                    fill_price=parsed.value,
                    fill_price_confidence=ocr.confidence,
                    fill_price_source="position_ocr",
                )

        # exhausted polling — either the region never updated or OCR never
        # returned a new parseable number. Don't confirm the fill.
        if after is None:
            return AckSignal(status="unknown", message="timeout",
                             fill_price_source="timeout")
        sim = iu.similarity_score(before, after)
        if 1.0 - sim >= self.change_threshold:
            # pixels changed but OCR couldn't extract a number → cautious unknown
            return AckSignal(status="unknown",
                             message="changed_but_unparsed",
                             evidence_image=after,
                             fill_price_source="stale")
        return AckSignal(status="unknown", message="no_visible_change",
                         evidence_image=after, fill_price_source="stale")

    def _get_ocr_reader(self) -> Optional[OCRReader]:
        if self._ocr_reader is not None:
            return self._ocr_reader
        if self._ocr_lazy_attempted:
            return None
        self._ocr_lazy_attempted = True
        try:
            self._ocr_reader = build_reader("tesseract")
        except Exception as e:
            log.warning("ack: OCR reader unavailable, falling back to diff-only: %s", e)
            self._ocr_reader = None
        return self._ocr_reader

    @staticmethod
    def _ocr_text(reader: OCRReader, img: np.ndarray) -> str:
        try:
            return (reader.read(img).raw_text or "").strip()
        except Exception:
            return ""
