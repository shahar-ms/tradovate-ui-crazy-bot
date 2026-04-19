from __future__ import annotations

import logging
import threading
from typing import Optional

import mss
import numpy as np

from app.models.common import Region
from app.utils import image_utils as iu

log = logging.getLogger(__name__)


class ScreenCapture:
    """
    Thin wrapper around mss that captures either a full monitor or a sub-region.

    mss is NOT thread-safe on Windows — the `mss.mss()` instance stores its
    source device context in thread-local storage, and calling grab() from a
    different thread raises
        '_thread._local' object has no attribute 'srcdc'
    To keep a single ScreenCapture object usable from multiple worker threads
    (executor_loop, watchdog_loop, command_drain_loop, …) we lazily create a
    per-thread mss session on first use.
    """

    def __init__(self, monitor_index: int):
        self.monitor_index = monitor_index
        self._local = threading.local()

    # --- per-thread lazy init --- #

    def _ensure(self) -> None:
        if not hasattr(self._local, "sct"):
            sct = mss.mss()
            if self.monitor_index >= len(sct.monitors) or self.monitor_index < 1:
                raise RuntimeError(
                    f"monitor_index {self.monitor_index} not available "
                    f"(found {len(sct.monitors) - 1} physical monitors)"
                )
            self._local.sct = sct
            self._local.monitor = sct.monitors[self.monitor_index]

    # --- public API (backwards compatible) --- #

    def grab_monitor(self) -> np.ndarray:
        self._ensure()
        raw = np.array(self._local.sct.grab(self._local.monitor))
        return iu.bgra_to_bgr(raw)

    def grab_region(self, region: Region) -> np.ndarray:
        self._ensure()
        mon = self._local.monitor
        grab = {
            "left": mon["left"] + region.left,
            "top": mon["top"] + region.top,
            "width": region.width,
            "height": region.height,
        }
        raw = np.array(self._local.sct.grab(grab))
        return iu.bgra_to_bgr(raw)

    def close(self) -> None:
        """Close the current thread's mss session, if any. Sessions on other
        threads will be cleaned up when those threads exit."""
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            try:
                del self._local.sct
                del self._local.monitor
            except AttributeError:
                pass

    def __enter__(self) -> "ScreenCapture":
        self._ensure()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
