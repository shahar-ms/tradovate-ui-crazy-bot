"""
Polls a calibrated "position size" rectangle and surfaces its integer value.

Source-of-truth design: instead of guessing whether a BUY/SELL click filled
by image-diffing the broker's position panel (fragile, often returns
"unknown" and forced a HALT), the bot simply reads the number the broker
itself is displaying. 0 = flat, >0 = in a trade.

Same cadence as PriceStream — one grab + OCR per tick.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

import numpy as np

from app.models.common import Region
from app.models.config import BotConfig
from app.utils.time_utils import now_ms

from . import preprocess
from .ocr_reader import OCRReader, build_reader
from .screen_capture import ScreenCapture

log = logging.getLogger(__name__)


_INT_RE = re.compile(r"-?\d+")


def parse_position_size(raw_text: str) -> Optional[int]:
    """
    Return the first non-negative integer found in the OCR text, or None
    when the crop is clearly unparseable. An empty / whitespace crop means
    the broker hasn't rendered anything there — treat that as 0 (flat).
    """
    # A None or empty crop (broker renders nothing when flat) → size 0.
    if raw_text is None:
        return 0
    text = raw_text.strip()
    if not text:
        return 0
    m = _INT_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        value = int(m.group(0))
    except ValueError:
        return None
    # A tiny negative value from OCR noise shouldn't flip us into a trade.
    return max(0, abs(value)) if -10 <= value <= 10000 else None


class PositionWatcher:
    """
    Threaded poller. Captures `region` at ~capture_fps_target, OCRs it,
    invokes on_size(int) whenever the parsed value CHANGES (including
    None -> 0 on first successful read).
    """

    def __init__(
        self,
        region: Region,
        monitor_index: int,
        bot_cfg: BotConfig,
        on_size: Callable[[int], None],
        reader: Optional[OCRReader] = None,
    ):
        self.region = region
        self.monitor_index = monitor_index
        self.cfg = bot_cfg
        self.on_size = on_size
        self.reader = reader or build_reader(bot_cfg.ocr_backend)

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.last_size: Optional[int] = None
        self.last_raw_text: str = ""
        self.last_update_ts_ms: int = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="position-watcher"
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        log.info("PositionWatcher starting (region=%s)", self.region.model_dump())
        period = 1.0 / max(1, self.cfg.capture_fps_target)
        capture = ScreenCapture(self.monitor_index)
        try:
            with capture:
                while not self._stop.is_set():
                    loop_start = time.time()
                    try:
                        img = capture.grab_region(self.region)
                    except Exception as e:
                        log.warning("position capture failed: %s", e)
                        time.sleep(period)
                        continue

                    size = self._ocr_size(img)
                    if size is not None and size != self.last_size:
                        self.last_size = size
                        self.last_update_ts_ms = now_ms()
                        try:
                            self.on_size(size)
                        except Exception:
                            log.exception("position on_size callback raised")

                    elapsed = time.time() - loop_start
                    sleep_for = period - elapsed
                    if sleep_for > 0:
                        time.sleep(sleep_for)
        finally:
            log.info("PositionWatcher exiting")

    def _ocr_size(self, img: np.ndarray) -> Optional[int]:
        # Single cheap preprocess step; the numbers on a broker panel are
        # high-contrast so more recipes rarely help.
        variants = preprocess.make_variants(img, ["otsu_threshold"])
        if not variants:
            variants = {"raw": img}
        best_text = ""
        best_conf = -1.0
        for _, variant_img in variants.items():
            try:
                ocr = self.reader.read(variant_img)
            except Exception:
                continue
            if ocr.raw_text and ocr.confidence > best_conf:
                best_text = ocr.raw_text
                best_conf = ocr.confidence
        self.last_raw_text = best_text
        return parse_position_size(best_text)
