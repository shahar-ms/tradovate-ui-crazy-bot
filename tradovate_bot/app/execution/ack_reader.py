"""
Acknowledgement reader. Tries to detect "something changed after my click"
using whatever evidence is available (status / position region diff).

Never pretends success without evidence. Returns "unknown" by default.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.capture.screen_capture import ScreenCapture
from app.models.common import Region, ScreenMap
from app.utils import image_utils as iu

log = logging.getLogger(__name__)


@dataclass
class AckSignal:
    status: str  # "ok" | "failed" | "unknown"
    message: str = ""
    evidence_image: Optional[np.ndarray] = None


class AckReader:
    """
    Captures a "before" snapshot of a chosen evidence region (if any), then
    after a wait, captures "after" and compares pixel difference.

    If no evidence region is configured, returns "unknown".
    """

    def __init__(
        self,
        screen_map: ScreenMap,
        capture: Optional[ScreenCapture] = None,
        wait_ms: int = 400,
        change_threshold: float = 0.02,
    ):
        self.screen_map = screen_map
        self._external_capture = capture is not None
        self._capture = capture or ScreenCapture(screen_map.monitor_index)
        self.wait_ms = wait_ms
        self.change_threshold = change_threshold

    def _evidence_region(self, action: str) -> Optional[Region]:
        if action == "CANCEL_ALL":
            return self.screen_map.status_region or self.screen_map.position_region
        return self.screen_map.position_region or self.screen_map.status_region

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
            time.sleep(self.wait_ms / 1000.0)
            return AckSignal(status="unknown", message="no_evidence_region")

        time.sleep(self.wait_ms / 1000.0)
        try:
            after = self._capture.grab_region(region)
        except Exception as e:
            return AckSignal(status="unknown", message=f"capture_after_failed:{e}")

        sim = iu.similarity_score(before, after)
        delta = 1.0 - sim
        if delta >= self.change_threshold:
            return AckSignal(status="ok", message=f"delta={delta:.3f}", evidence_image=after)
        return AckSignal(status="unknown", message=f"no_visible_change:delta={delta:.3f}",
                         evidence_image=after)

    def close(self) -> None:
        if not self._external_capture:
            self._capture.close()
