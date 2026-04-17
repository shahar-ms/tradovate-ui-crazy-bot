from __future__ import annotations

import logging
from typing import Optional

import mss
import numpy as np

from app.models.common import Region
from app.utils import image_utils as iu

log = logging.getLogger(__name__)


class ScreenCapture:
    """
    Thin wrapper around mss that captures either a full monitor or a sub-region.

    mss sessions are not shared between threads, so this class keeps its own
    mss.mss() instance per ScreenCapture object.
    """

    def __init__(self, monitor_index: int):
        self.monitor_index = monitor_index
        self._sct: Optional[mss.mss] = None
        self._monitor: Optional[dict] = None

    def _ensure(self) -> None:
        if self._sct is None:
            self._sct = mss.mss()
            if self.monitor_index >= len(self._sct.monitors) or self.monitor_index < 1:
                raise RuntimeError(
                    f"monitor_index {self.monitor_index} not available "
                    f"(found {len(self._sct.monitors) - 1} physical monitors)"
                )
            self._monitor = self._sct.monitors[self.monitor_index]

    def grab_monitor(self) -> np.ndarray:
        self._ensure()
        raw = np.array(self._sct.grab(self._monitor))
        return iu.bgra_to_bgr(raw)

    def grab_region(self, region: Region) -> np.ndarray:
        self._ensure()
        grab = {
            "left": self._monitor["left"] + region.left,
            "top": self._monitor["top"] + region.top,
            "width": region.width,
            "height": region.height,
        }
        raw = np.array(self._sct.grab(grab))
        return iu.bgra_to_bgr(raw)

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
            self._monitor = None

    def __enter__(self) -> "ScreenCapture":
        self._ensure()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
