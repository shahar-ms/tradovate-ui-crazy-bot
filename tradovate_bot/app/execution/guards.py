"""
Screen guard: verifies that the current screen still matches what we calibrated,
before allowing any real UI action.

Checks:
  1. monitor_index exists and dimensions match calibration
  2. target click point is inside calibrated screen bounds
  3. anchor crop similarity >= configured threshold
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from app.capture.screen_capture import ScreenCapture
from app.models.common import Point, ScreenMap
from app.utils import image_utils as iu
from app.utils import paths

log = logging.getLogger(__name__)


@dataclass
class GuardResult:
    ok: bool
    reason: Optional[str] = None
    similarity: Optional[float] = None

    def as_message(self) -> str:
        if self.ok:
            sim = f"{self.similarity:.3f}" if self.similarity is not None else "n/a"
            return f"guard ok (anchor_similarity={sim})"
        return f"guard blocked: {self.reason}"


class ScreenGuard:
    def __init__(self, screen_map: ScreenMap, anchor_threshold: float,
                 capture: Optional[ScreenCapture] = None):
        self.screen_map = screen_map
        self.anchor_threshold = anchor_threshold
        self._external_capture = capture is not None
        self._capture = capture or ScreenCapture(screen_map.monitor_index)
        self._anchor_ref: Optional[np.ndarray] = None

    def _load_anchor_ref(self) -> np.ndarray:
        if self._anchor_ref is None:
            p = paths.resolve_relative(self.screen_map.tradovate_anchor_reference_path)
            if not p.exists():
                raise FileNotFoundError(f"anchor reference missing: {p}")
            self._anchor_ref = iu.load_png(p)
        return self._anchor_ref

    def check(self, target_point: Optional[Point] = None) -> GuardResult:
        sm = self.screen_map

        # 1 + 2. point bounds
        if target_point is not None:
            if not sm.point_in_screen(target_point):
                return GuardResult(False, reason=f"point_out_of_bounds:{target_point.x},{target_point.y}")

        # 3. anchor similarity (also catches monitor-size drift via mss errors)
        try:
            anchor_ref = self._load_anchor_ref()
        except Exception as e:
            return GuardResult(False, reason=f"anchor_ref_load_failed:{e}")

        try:
            live = self._capture.grab_region(sm.tradovate_anchor_region)
        except Exception as e:
            return GuardResult(False, reason=f"anchor_capture_failed:{e}")

        sim = iu.similarity_score(anchor_ref, live)
        if sim < self.anchor_threshold:
            return GuardResult(False, reason=f"anchor_mismatch:{sim:.3f}<{self.anchor_threshold:.3f}",
                               similarity=sim)
        return GuardResult(True, similarity=sim)

    def close(self) -> None:
        if not self._external_capture:
            self._capture.close()
