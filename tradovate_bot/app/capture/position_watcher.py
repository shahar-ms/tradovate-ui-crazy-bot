"""
Polls calibrated broker-panel cells to mirror the live position state.

Two independent watchers, one per cell:
  - PositionWatcher:   signed integer ('1' / '-1' / '0' / empty=flat). The
    sign gives us side (+=long, -=short) so we don't need a separate
    long/short indicator.
  - EntryPriceWatcher: verified broker fill price while in-position.
    Empty / unparseable cell -> None (reported as 'no verified fill' on
    the HUD; PnL shows '—' until a clean read arrives).

Source-of-truth design: instead of guessing whether a BUY/SELL click filled
by image-diffing the broker's position panel (fragile, often returns
'unknown' and forced a HALT), the bot simply reads what the broker itself
is displaying. 0 contracts = flat, nonzero = in a trade.

Splitting size and entry price across two regions keeps each OCR crop
simple (plain int + plain decimal), which is much more reliable than a
combined 'size@price' glyph that has to survive '@' mis-reads and mixed
character types.

Same cadence as PriceStream — one grab + OCR per tick per watcher.
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
from .parser import parse_price
from .screen_capture import ScreenCapture

log = logging.getLogger(__name__)


_INT_RE = re.compile(r"-?\d+")


def parse_position_size(raw_text: Optional[str]) -> Optional[int]:
    """
    Return the SIGNED integer found in the OCR text:
      - empty / whitespace crop -> 0 (broker renders nothing when flat)
      - '0' -> 0
      - '1' -> 1 (long)
      - '-1' -> -1 (short)
      - unparseable -> None (the caller should keep the previous value)

    The sign is the side indicator; callers derive long/short from it.
    """
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
    # MNQ contract counts are a small handful; anything wildly large is OCR
    # bleed from a neighboring cell.
    if not -10000 <= value <= 10000:
        return None
    return value


class PositionWatcher:
    """
    Threaded poller for the position-size cell. Captures `region` at
    ~capture_fps_target, OCRs it, invokes on_size(int) whenever the parsed
    SIGNED value changes (including None -> 0 on first successful read).
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
        # Throttle WARNING logs on unparseable OCR so a bad calibration
        # doesn't spam the log.
        self._unparseable_log_ts: float = 0.0
        self._UNPARSEABLE_LOG_INTERVAL_S: float = 10.0

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
                    if size is None:
                        now = time.time()
                        if now - self._unparseable_log_ts >= self._UNPARSEABLE_LOG_INTERVAL_S:
                            self._unparseable_log_ts = now
                            log.warning(
                                "position-size OCR unparseable (raw_text=%r) — "
                                "recalibrate the region or verify it contains an integer",
                                self.last_raw_text,
                            )
                    elif size != self.last_size:
                        self.last_size = size
                        self.last_update_ts_ms = now_ms()
                        log.info("position-size OCR: %d (raw=%r)",
                                 size, self.last_raw_text)
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
        # Small integer glyphs need scaling to survive OCR. Try a couple of
        # recipes and prefer a parseable result over a higher-confidence
        # but unparseable one.
        variants = preprocess.make_variants(
            img,
            ["scaled_2x_otsu", "otsu_threshold"],
        )
        if not variants:
            variants = {"raw": img}
        best_parsed: Optional[int] = None
        best_parsed_text = ""
        best_text = ""
        best_conf = -1.0
        for _, variant_img in variants.items():
            try:
                ocr = self.reader.read(variant_img)
            except Exception:
                continue
            if not ocr.raw_text:
                continue
            parsed = parse_position_size(ocr.raw_text)
            if parsed is not None and best_parsed is None:
                best_parsed = parsed
                best_parsed_text = ocr.raw_text
            if ocr.confidence > best_conf:
                best_text = ocr.raw_text
                best_conf = ocr.confidence
        self.last_raw_text = best_parsed_text or best_text
        return best_parsed if best_parsed is not None else parse_position_size(best_text)


class EntryPriceWatcher:
    """
    Threaded poller for the entry-price cell. Captures `region` at the
    same cadence as PositionWatcher, OCRs it with the same parse_price
    pipeline used by the main price stream, and invokes on_price(price)
    whenever the parsed value CHANGES.

    `price` is Optional[float]: None when the cell is empty/unparseable
    (typically: flat). The caller uses that to clear the stale fill.
    """

    def __init__(
        self,
        region: Region,
        monitor_index: int,
        bot_cfg: BotConfig,
        on_price: Callable[[Optional[float]], None],
        reader: Optional[OCRReader] = None,
    ):
        self.region = region
        self.monitor_index = monitor_index
        self.cfg = bot_cfg
        self.on_price = on_price
        self.reader = reader or build_reader(bot_cfg.ocr_backend)

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.last_price: Optional[float] = None
        self._last_emitted: bool = False   # whether we've emitted anything yet
        self.last_raw_text: str = ""
        self.last_update_ts_ms: int = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="entry-price-watcher"
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def invalidate(self) -> None:
        """Force the next successful OCR to fire on_price, even if the
        parsed value is unchanged. Used on a position side-flip: the
        supervisor just cleared state.last_fill_price and needs the next
        read — which will probably match this watcher's cached last_price —
        to refresh state so the HUD's PnL line recovers."""
        self.last_price = None
        self._last_emitted = False

    def _run(self) -> None:
        log.info("EntryPriceWatcher starting (region=%s)", self.region.model_dump())
        period = 1.0 / max(1, self.cfg.capture_fps_target)
        capture = ScreenCapture(self.monitor_index)
        try:
            with capture:
                while not self._stop.is_set():
                    loop_start = time.time()
                    try:
                        img = capture.grab_region(self.region)
                    except Exception as e:
                        log.warning("entry-price capture failed: %s", e)
                        time.sleep(period)
                        continue

                    price = self._ocr_price(img)
                    # Emit on any change, including the None <-> float edge
                    # so the supervisor can clear stale fill prices when the
                    # cell goes blank (flat).
                    if not self._last_emitted or price != self.last_price:
                        self._last_emitted = True
                        self.last_price = price
                        self.last_update_ts_ms = now_ms()
                        log.info("entry-price OCR: %s (raw=%r)",
                                 price, self.last_raw_text)
                        try:
                            self.on_price(price)
                        except Exception:
                            log.exception("entry-price on_price callback raised")

                    elapsed = time.time() - loop_start
                    sleep_for = period - elapsed
                    if sleep_for > 0:
                        time.sleep(sleep_for)
        finally:
            log.info("EntryPriceWatcher exiting")

    def _ocr_price(self, img: np.ndarray) -> Optional[float]:
        variants = preprocess.make_variants(
            img,
            ["scaled_3x_binary_close", "scaled_2x_otsu", "otsu_threshold"],
        )
        if not variants:
            variants = {"raw": img}
        best_parsed: Optional[float] = None
        best_parsed_text = ""
        best_text = ""
        best_conf = -1.0
        for _, variant_img in variants.items():
            try:
                ocr = self.reader.read(variant_img)
            except Exception:
                continue
            if not ocr.raw_text:
                continue
            result = parse_price(ocr.raw_text)
            if result.ok and best_parsed is None:
                best_parsed = result.value
                best_parsed_text = ocr.raw_text
            if ocr.confidence > best_conf:
                best_text = ocr.raw_text
                best_conf = ocr.confidence
        self.last_raw_text = best_parsed_text or best_text
        if best_parsed is not None:
            return best_parsed
        # Empty / missing cell when flat: return None (no verified fill).
        if not best_text.strip():
            return None
        # Non-empty but unparseable: also None, but keep raw_text around
        # for the operator to see.
        return None
