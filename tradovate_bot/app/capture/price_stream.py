"""
Price stream loop. Captures the price region repeatedly, runs preprocessing
+ OCR across several recipes, votes, validates, and publishes a PriceTick
(accepted or rejected).
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from app.models.common import Region
from app.models.config import BotConfig
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.time_utils import now_ms

from . import preprocess
from .health import HealthConfig, HealthTracker
from .models import PriceTick, StreamHealth
from .ocr_reader import OCRReader, build_reader
from .parser import parse_price
from .screen_capture import ScreenCapture
from .validator import PriceValidator
from .voting import Candidate, vote

log = logging.getLogger(__name__)


def _is_blank_crop(img: np.ndarray, std_threshold: float = 5.0) -> bool:
    """True when the crop has almost no tonal variation — Tradovate's price
    cell occasionally renders as a solid dark rectangle during repaints, and
    we don't want those frames to count as OCR failures."""
    if img is None or img.size == 0:
        return True
    # standard deviation across the whole crop: a real price cell with
    # lit digits has std ~30+; a blank/near-solid cell sits under 5.
    return float(img.std()) < std_threshold


@dataclass
class OneFrameResult:
    tick: PriceTick
    candidates: list[Candidate]


class PriceStream:
    """
    Threaded price capture loop.

    Usage:
        stream = PriceStream(region=..., monitor_index=..., bot_cfg=...)
        stream.start()
        tick = stream.get_latest_tick()
        for t in stream.drain_accepted(): ...
        stream.stop()
    """

    def __init__(
        self,
        region: Region,
        monitor_index: int,
        bot_cfg: BotConfig,
        reader: Optional[OCRReader] = None,
        on_tick: Optional[Callable[[PriceTick], None]] = None,
    ):
        self.region = region
        self.monitor_index = monitor_index
        self.cfg = bot_cfg
        self.reader = reader or build_reader(bot_cfg.ocr_backend)
        self.on_tick = on_tick

        self.validator = PriceValidator(
            min_confidence=bot_cfg.min_ocr_confidence,
            tick_size=0.25,
            max_jump_points=bot_cfg.max_jump_points,
        )
        self.health = HealthTracker(HealthConfig(stale_ms=bot_cfg.price_stale_ms))

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._latest_tick: Optional[PriceTick] = None
        self._frame_id = 0
        self._accepted_queue: "queue.Queue[PriceTick]" = queue.Queue(maxsize=1024)
        self._last_debug_save_ts = 0
        self._lock = threading.Lock()

        # cumulative counters + diagnostic fields (read by UI layer)
        self.total_accepted_count: int = 0
        self.total_rejected_count: int = 0
        self.last_raw_text: str = ""
        self.last_reject_reason: Optional[str] = None

        # Frame-dedup cache — OCR is the expensive part. If the raw crop is
        # byte-identical to the previous frame, reuse the previous tick (just
        # update timestamp + frame_id). Common when prices are static between
        # actual updates (prices update ~1/sec, we sample much faster).
        self._last_frame_hash: Optional[bytes] = None
        self.total_deduped_count: int = 0

        # Debug: wall-clock ms spent on the last frame that actually ran OCR.
        # Not reset on dedup hits (the user wants "how long does it take to
        # OCR this price region", not "how long did the hash check take").
        self.last_ocr_ms: float = 0.0
        # Debug: full end-to-end per-frame time measured on the client side
        # — from the moment we grab the crop to the moment the PriceTick is
        # published and available to the UI. Includes capture + dedup check +
        # OCR (if it ran) + validate + publish. Refreshed every frame.
        self.last_frame_ms: float = 0.0

        # Parallel OCR across preprocess recipes. Tesseract runs a subprocess
        # per call, so the calls are IO-bound; a small thread pool collapses
        # the sum of their latencies into roughly the slowest single one.
        n_workers = max(1, len(bot_cfg.preprocess_recipes))
        self._ocr_pool = ThreadPoolExecutor(
            max_workers=min(n_workers, 4),
            thread_name_prefix="ocr",
        )

    # --- thread control --- #

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="price-stream")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        try:
            self._ocr_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # --- consumers --- #

    def get_latest_tick(self) -> Optional[PriceTick]:
        with self._lock:
            return self._latest_tick

    def get_health(self) -> StreamHealth:
        return self.health.snapshot()

    def drain_accepted(self, max_items: int = 128) -> list[PriceTick]:
        out: list[PriceTick] = []
        for _ in range(max_items):
            try:
                out.append(self._accepted_queue.get_nowait())
            except queue.Empty:
                break
        return out

    # --- loop body (also reusable in replay) --- #

    def process_image(self, img: np.ndarray, frame_id: Optional[int] = None,
                      source_path: Optional[str] = None) -> OneFrameResult:
        fid = frame_id if frame_id is not None else self._next_frame_id()

        # ---------- frame-dedup fast path ---------- #
        # If the raw crop pixels are byte-identical to the previous frame,
        # reuse the previous tick — OCR is the expensive part and the result
        # won't have changed. Keeps the published stream "alive" at the
        # capture cadence without paying OCR cost on every tick.
        crop_hash = hashlib.blake2b(img.tobytes(), digest_size=16).digest()
        if crop_hash == self._last_frame_hash and self._latest_tick is not None:
            self.total_deduped_count += 1
            prev = self._latest_tick
            dup = prev.model_copy(update={
                "ts_ms": now_ms(),
                "frame_id": fid,
                "source_image_path": source_path,
            })
            if dup.accepted:
                self.health.on_success(dup.price)  # type: ignore[arg-type]
                self.total_accepted_count += 1
            else:
                # no need to re-count a rejection we already counted
                pass
            with self._lock:
                self._latest_tick = dup
            if dup.accepted:
                try:
                    self._accepted_queue.put_nowait(dup)
                except queue.Full:
                    try:
                        self._accepted_queue.get_nowait()
                        self._accepted_queue.put_nowait(dup)
                    except queue.Empty:
                        pass
            if self.on_tick:
                try:
                    self.on_tick(dup)
                except Exception:
                    log.exception("on_tick callback raised")
            return OneFrameResult(tick=dup, candidates=[])
        self._last_frame_hash = crop_hash

        # ---------- blank-crop skip ---------- #
        # Tradovate's price cell briefly renders nothing (repaints, tooltip
        # overlays, DOM rebuild) — the crop comes back as a near-solid dark
        # rectangle. That is NOT an OCR failure, the price is just not on
        # screen right now. Without this skip, a few seconds of blanks
        # accumulate past break_after_consecutive_failures and the bot
        # pauses on "price_stream_broken" despite the underlying stream
        # being healthy. Don't count, don't OCR, reuse the last good tick.
        if _is_blank_crop(img) and self._latest_tick is not None:
            prev = self._latest_tick
            dup = prev.model_copy(update={
                "ts_ms": now_ms(), "frame_id": fid,
                "source_image_path": source_path,
            })
            with self._lock:
                self._latest_tick = dup
            # keep health alive if we had a last accepted price
            if prev.accepted and prev.price is not None:
                self.health.on_success(prev.price)
            return OneFrameResult(tick=dup, candidates=[])

        # Measure wall-clock from this point through the OCR + vote phase.
        # Preprocessing is included because it's part of the per-frame
        # "what does this pixel crop say" cost the operator cares about.
        t_start = time.time()

        variants = preprocess.make_variants(img, self.cfg.preprocess_recipes)

        prev = self.health.state.last_accepted_price
        candidates: list[Candidate] = []
        best_failed_raw = ""
        best_failed_conf = 0.0
        failed_reasons: list[str] = []

        # ---------- parallel OCR across recipes ---------- #
        # Tesseract runs a subprocess per call, so the calls are IO-bound.
        # Dispatching them to a small thread pool collapses the sum of
        # their latencies into ~the slowest single one.
        recipe_names = list(variants.keys())
        variant_imgs = [variants[n] for n in recipe_names]
        if len(recipe_names) == 1:
            ocr_results = [self.reader.read(variant_imgs[0])]
        else:
            futures = [self._ocr_pool.submit(self.reader.read, v) for v in variant_imgs]
            ocr_results = [f.result() for f in futures]

        for recipe_name, ocr in zip(recipe_names, ocr_results):
            if ocr.raw_text and ocr.confidence > best_failed_conf:
                best_failed_raw = ocr.raw_text
                best_failed_conf = ocr.confidence

            parsed = parse_price(ocr.raw_text)
            if not parsed.ok:
                failed_reasons.append(parsed.reason or "parse_failed")
                continue
            verdict = self.validator.check(parsed.value, ocr.confidence, prev)
            if verdict.accepted and verdict.value is not None:
                candidates.append(Candidate(
                    price=verdict.value,
                    confidence=ocr.confidence,
                    recipe=recipe_name,
                    raw_text=ocr.raw_text,
                ))
            else:
                failed_reasons.append(verdict.reason or "rejected")

        result = vote(candidates)

        # stash the wall-clock cost of this real OCR pass for the HUD
        self.last_ocr_ms = (time.time() - t_start) * 1000.0

        if result.accepted and result.price is not None:
            self.health.on_success(result.price)
            self.total_accepted_count += 1
            self.last_raw_text = result.raw_text or ""
            self.last_reject_reason = None
            tick = PriceTick(
                ts_ms=now_ms(),
                frame_id=fid,
                raw_text=result.raw_text or "",
                price=result.price,
                confidence=result.confidence,
                accepted=True,
                reject_reason=None,
                recipe=result.recipe,
                source_image_path=source_path,
            )
        else:
            reason = self._best_reason(result.reason, failed_reasons)
            self.health.on_rejection(reason)
            self.total_rejected_count += 1
            self.last_raw_text = best_failed_raw
            self.last_reject_reason = reason
            tick = PriceTick(
                ts_ms=now_ms(),
                frame_id=fid,
                raw_text=best_failed_raw,
                price=None,
                confidence=best_failed_conf,
                accepted=False,
                reject_reason=reason,
                recipe=None,
                source_image_path=source_path,
            )

        with self._lock:
            self._latest_tick = tick
        if tick.accepted:
            try:
                self._accepted_queue.put_nowait(tick)
            except queue.Full:
                log.warning("accepted queue full; dropping oldest")
                try:
                    self._accepted_queue.get_nowait()
                    self._accepted_queue.put_nowait(tick)
                except queue.Empty:
                    pass
        if self.on_tick:
            try:
                self.on_tick(tick)
            except Exception:
                log.exception("on_tick callback raised")
        return OneFrameResult(tick=tick, candidates=candidates)

    # --- internals --- #

    _REASON_PRIORITY = (
        "jump_too_large",
        "not_tick_aligned",
        "implausible_range",
        "low_confidence",
        "parse_failed",
        "candidates_disagree",
    )

    def _best_reason(self, voter_reason: Optional[str], per_recipe_reasons: list[str]) -> str:
        """Pick the most specific rejection reason across all recipes."""
        pool = list(per_recipe_reasons)
        if voter_reason:
            pool.append(voter_reason)
        if not pool:
            return "no_valid_candidates"
        for prefix in self._REASON_PRIORITY:
            for r in pool:
                if r.startswith(prefix):
                    return r
        return pool[0]

    def _next_frame_id(self) -> int:
        self._frame_id += 1
        return self._frame_id

    def _run(self) -> None:
        log.info("PriceStream loop starting (fps=%d, region=%s)",
                 self.cfg.capture_fps_target, self.region.model_dump())
        period = 1.0 / max(1, self.cfg.capture_fps_target)
        capture = ScreenCapture(self.monitor_index)
        try:
            with capture:
                while not self._stop.is_set():
                    loop_start = time.time()
                    try:
                        img = capture.grab_region(self.region)
                    except Exception as e:
                        log.warning("capture failed: %s", e)
                        self.health.on_failure()
                        time.sleep(period)
                        continue

                    fid = self._next_frame_id()
                    self.process_image(img, frame_id=fid)
                    # End-to-end per-frame time: what the operator sees as the
                    # delay between "price changed on screen" and "HUD shows
                    # the new number". Captured right after the tick is
                    # published so it reflects the full client-side pipeline.
                    self.last_frame_ms = (time.time() - loop_start) * 1000.0
                    self.health.tick_for_staleness()

                    self._maybe_save_debug(img, fid)

                    elapsed = time.time() - loop_start
                    sleep_for = period - elapsed
                    if sleep_for > 0:
                        time.sleep(sleep_for)
        finally:
            log.info("PriceStream loop exiting")

    def _maybe_save_debug(self, img: np.ndarray, frame_id: int) -> None:
        if not self.cfg.save_debug_images:
            return
        now = time.time()
        if now - self._last_debug_save_ts < self.cfg.debug_image_interval_sec:
            return
        try:
            out = paths.screenshots_dir() / "debug_price" / f"frame_{frame_id:06d}.png"
            iu.save_png(img, out)
            self._last_debug_save_ts = now
        except Exception as e:
            log.debug("debug image save failed: %s", e)
