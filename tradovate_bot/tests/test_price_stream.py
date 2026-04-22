"""
End-to-end test of the price stream pipeline with a deterministic stub reader.

No Tesseract dependency is needed here because we inject a custom reader
whose output can be scripted per recipe.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from app.capture.models import OCRResult
from app.capture.price_stream import PriceStream
from app.models.common import Region
from app.models.config import BotConfig


class ScriptedReader:
    """Returns OCR outputs in order, cycling as needed."""

    def __init__(self, outputs: list[tuple[str, float]]):
        self._it = itertools.cycle(outputs)

    def read(self, image):  # noqa: ARG002
        raw, conf = next(self._it)
        return OCRResult(raw_text=raw, confidence=conf, engine_name="scripted")


class PerRecipeReader:
    """Returns a different OCR output for each recipe.

    The pipeline iterates recipes in dict order and calls read() once per variant.
    We count calls modulo len(recipes) to assign outputs.
    """

    def __init__(self, recipe_outputs: dict[str, tuple[str, float]]):
        self.recipe_outputs = recipe_outputs
        self._i = 0
        self._order = list(recipe_outputs.keys())

    def read(self, image):  # noqa: ARG002
        name = self._order[self._i % len(self._order)]
        self._i += 1
        raw, conf = self.recipe_outputs[name]
        return OCRResult(raw_text=raw, confidence=conf, engine_name="per-recipe")


def _cfg(recipes: list[str]) -> BotConfig:
    return BotConfig(
        capture_fps_target=8,
        ocr_backend="tesseract",
        min_ocr_confidence=70.0,
        price_stale_ms=1500,
        anchor_match_threshold=0.9,
        click_move_duration_ms=50,
        click_post_delay_ms=50,
        max_consecutive_failures=10,
        paper_mode_default=True,
        save_debug_images=False,
        debug_image_interval_sec=10,
        max_jump_points=30.0,
        preprocess_recipes=recipes,
    )


def test_process_image_accepts_agreeing_candidates():
    recipes = ["gray_only", "otsu_threshold"]
    reader = PerRecipeReader({
        "gray_only": ("19234.25", 85.0),
        "otsu_threshold": ("19234.25", 92.0),
    })
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    result = stream.process_image(img)
    assert result.tick.accepted
    assert result.tick.price == 19234.25
    assert result.tick.confidence == 92.0
    assert stream.get_health().health_state == "ok"


def test_process_image_rejects_disagreement():
    recipes = ["gray_only", "otsu_threshold"]
    reader = PerRecipeReader({
        "gray_only": ("19234.25", 85.0),
        "otsu_threshold": ("19234.50", 88.0),
    })
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    result = stream.process_image(img)
    assert not result.tick.accepted
    assert result.tick.reject_reason == "candidates_disagree"


def test_process_image_rejects_off_tick():
    recipes = ["gray_only"]
    reader = PerRecipeReader({"gray_only": ("19234.17", 90.0)})
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    result = stream.process_image(img)
    assert not result.tick.accepted


def test_process_image_rejects_garbage_text():
    recipes = ["gray_only"]
    reader = PerRecipeReader({"gray_only": ("19a34.25", 95.0)})
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    result = stream.process_image(img)
    assert not result.tick.accepted


def test_accepted_ticks_drain():
    recipes = ["gray_only"]
    reader = PerRecipeReader({"gray_only": ("19234.25", 90.0)})
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(5):
        stream.process_image(img)
    ticks = stream.drain_accepted()
    assert len(ticks) == 5
    assert all(t.accepted for t in ticks)


def test_identical_frame_is_deduped_without_rerunning_ocr():
    """When the raw crop is byte-identical to the previous frame, OCR is
    skipped and the previous tick is reused — this is the dominant fast
    path at high capture rates."""
    recipes = ["gray_only"]
    reader = PerRecipeReader({"gray_only": ("19234.25", 90.0)})
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    r1 = stream.process_image(img)
    r2 = stream.process_image(img)  # identical → should dedup
    r3 = stream.process_image(img)  # identical → should dedup

    assert r1.tick.accepted
    assert r2.tick.accepted
    assert r3.tick.accepted
    # Same price surfaced on all three
    assert r1.tick.price == r2.tick.price == r3.tick.price == 19234.25
    # Two of the three processed frames were served from the dedup cache
    assert stream.total_deduped_count == 2
    # The reader was called exactly once (for the first frame only)
    assert reader._i == 1


def test_different_frame_bypasses_dedup():
    """A byte-different frame must fall through the dedup and run OCR."""
    recipes = ["gray_only"]
    reader = ScriptedReader([("19234.25", 90.0), ("19234.50", 90.0)])
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    # Non-uniform images so the blank-crop skip doesn't short-circuit OCR.
    img_a = np.zeros((10, 10, 3), dtype=np.uint8); img_a[0:5, :, :] = 200
    img_b = np.zeros((10, 10, 3), dtype=np.uint8); img_b[5:10, :, :] = 200

    r1 = stream.process_image(img_a)
    r2 = stream.process_image(img_b)

    assert r1.tick.price == 19234.25
    assert r2.tick.price == 19234.50
    assert stream.total_deduped_count == 0


def test_last_ocr_ms_populated_on_real_ocr_but_not_on_dedup():
    """The debug timer that feeds the HUD: last_ocr_ms reflects the last
    OCR pass. Dedup hits must NOT overwrite it (operator wants to know the
    OCR cost of the CURRENT number, not the trivial hash-check cost)."""
    recipes = ["gray_only"]
    reader = PerRecipeReader({"gray_only": ("19234.25", 90.0)})
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    # first frame: real OCR runs, timer should be populated
    stream.process_image(img)
    after_real = stream.last_ocr_ms
    assert after_real >= 0

    # second frame: identical, dedup path, last_ocr_ms must not change
    stream.process_image(img)
    assert stream.last_ocr_ms == after_real


def test_parallel_ocr_still_produces_correct_vote():
    """Using the internal ThreadPoolExecutor must not change semantics —
    three recipes agreeing still produce an accepted tick with the
    highest-confidence recipe winning."""
    recipes = ["gray_only", "otsu_threshold", "scaled_2x_otsu"]
    reader = PerRecipeReader({
        "gray_only":       ("19234.25", 85.0),
        "otsu_threshold":  ("19234.25", 92.0),
        "scaled_2x_otsu":  ("19234.25", 88.0),
    })
    cfg = _cfg(recipes)
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    r = stream.process_image(img)
    assert r.tick.accepted
    assert r.tick.price == 19234.25
    assert r.tick.confidence == 92.0


def test_jump_rejection_after_accepted_price():
    recipes = ["gray_only"]
    # First image produces 19234.25, second produces an unreasonable 19500.00
    outputs = [("19234.25", 90.0), ("19500.00", 90.0)]
    reader = ScriptedReader(outputs)
    cfg = _cfg(recipes)
    cfg = cfg.model_copy(update={"max_jump_points": 30.0})
    stream = PriceStream(
        region=Region(left=0, top=0, width=10, height=10),
        monitor_index=1,
        bot_cfg=cfg,
        reader=reader,
    )
    # Two pixel-distinct, non-uniform images so neither the dedup fast path
    # nor the blank-crop skip short-circuits OCR.
    img_a = np.zeros((10, 10, 3), dtype=np.uint8); img_a[0:5, :, :] = 200
    img_b = np.zeros((10, 10, 3), dtype=np.uint8); img_b[5:10, :, :] = 200
    r1 = stream.process_image(img_a)
    r2 = stream.process_image(img_b)
    assert r1.tick.accepted
    assert not r2.tick.accepted
    assert "jump_too_large" in (r2.tick.reject_reason or "")
