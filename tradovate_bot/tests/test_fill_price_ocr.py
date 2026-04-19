"""
AckReader fill-price OCR: deterministic tests using a scripted OCR reader.

We stub both the ScreenCapture (returns canned numpy images) and the
OCRReader (returns scripted OCRResult sequences), so we can verify the
polling loop independent of the real screen / Tesseract.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pytest

from app.capture.models import OCRResult
from app.execution.ack_reader import AckReader
from app.models.common import Point, Region, ScreenMap


class FakeCapture:
    """ScreenCapture stub that returns canned BGR images."""

    def __init__(self, images: list[np.ndarray]):
        self._images = images
        self._i = 0
        self.closed = False

    def grab_region(self, region):  # noqa: ARG002
        img = self._images[min(self._i, len(self._images) - 1)]
        self._i += 1
        return img

    def close(self):
        self.closed = True


class ScriptedReader:
    """OCRReader stub that returns a scripted OCRResult sequence."""

    def __init__(self, outputs: list[tuple[str, float]]):
        self._outputs = outputs
        self._i = 0

    def read(self, image):  # noqa: ARG002
        raw, conf = self._outputs[min(self._i, len(self._outputs) - 1)]
        self._i += 1
        return OCRResult(raw_text=raw, confidence=conf, engine_name="scripted")


def _sm(with_position: bool = True) -> ScreenMap:
    return ScreenMap(
        monitor_index=1,
        screen_width=1920,
        screen_height=1080,
        browser_name="chrome",
        tradovate_anchor_region=Region(left=10, top=10, width=80, height=20),
        tradovate_anchor_reference_path="runtime/screenshots/anchor_reference.png",
        price_region=Region(left=800, top=100, width=100, height=40),
        buy_point=Point(x=1500, y=900),
        sell_point=Point(x=1560, y=900),
        cancel_all_point=Point(x=1620, y=900),
        position_region=Region(left=1100, top=700, width=200, height=40) if with_position else None,
    )


def _blank(label: int) -> np.ndarray:
    # each image is distinguishable by its constant fill value
    img = np.full((40, 200, 3), label % 255, dtype=np.uint8)
    return img


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    """Shrink the polling windows so tests run fast."""
    monkeypatch.setattr(AckReader, "POLL_INTERVAL_MS", 1)
    monkeypatch.setattr(AckReader, "MAX_POLL_MS", 8)


def _make_reader(sm: ScreenMap, images: list[np.ndarray],
                 ocr_outputs: list[tuple[str, float]],
                 min_conf: float = 60.0) -> tuple[AckReader, FakeCapture, ScriptedReader]:
    cap = FakeCapture(images)
    reader = ScriptedReader(ocr_outputs)
    ack = AckReader(screen_map=sm, capture=cap, ocr_reader=reader,
                    min_ocr_confidence=min_conf)
    return ack, cap, reader


def test_fill_price_extracted_on_first_new_text():
    """The first post-click read whose text differs AND has high confidence wins."""
    sm = _sm()
    # capture sequence: [before, after1 (same text), after2 (new fill)]
    images = [_blank(0), _blank(1), _blank(2)]
    # OCR sequence: before -> previous_price, after1 -> previous_price, after2 -> 19234.25
    ocr_outputs = [
        ("19230.00", 90.0),   # before
        ("19230.00", 90.0),   # after1 (unchanged)
        ("19234.25", 92.0),   # after2 (new fill!)
    ]
    ack, _, _ = _make_reader(sm, images, ocr_outputs)
    before = ack.capture_before("BUY")
    signal = ack.read_after("BUY", before)
    assert signal.status == "ok"
    assert signal.fill_price == 19234.25
    assert signal.fill_price_source == "position_ocr"
    assert signal.fill_price_confidence == 92.0


def test_fill_price_rejected_when_confidence_below_threshold():
    sm = _sm()
    images = [_blank(0), _blank(1), _blank(2)]
    ocr_outputs = [
        ("19230.00", 95.0),   # before (high conf)
        ("19234.25", 40.0),   # post-click but low confidence
        ("19234.25", 45.0),   # still low confidence
    ]
    ack, _, _ = _make_reader(sm, images, ocr_outputs, min_conf=70.0)
    before = ack.capture_before("BUY")
    signal = ack.read_after("BUY", before)
    assert signal.fill_price is None
    assert signal.status == "unknown"
    assert signal.fill_price_source in ("stale", "timeout")


def test_stale_read_when_text_never_changes():
    sm = _sm()
    images = [_blank(0)] + [_blank(0)] * 10
    ocr_outputs = [("19230.00", 90.0)] * 12  # stuck on the old fill
    ack, _, _ = _make_reader(sm, images, ocr_outputs)
    before = ack.capture_before("BUY")
    signal = ack.read_after("BUY", before)
    assert signal.fill_price is None
    assert signal.status == "unknown"
    assert signal.fill_price_source == "stale"


def test_unparseable_text_does_not_fill():
    sm = _sm()
    images = [_blank(0), _blank(1)]
    ocr_outputs = [
        ("19230.00", 92.0),   # before
        ("garbage xyz", 80.0),  # after (high conf but unparseable)
    ]
    ack, _, _ = _make_reader(sm, images, ocr_outputs)
    before = ack.capture_before("BUY")
    signal = ack.read_after("BUY", before)
    assert signal.fill_price is None
    assert signal.status == "unknown"


def test_cancel_all_uses_diff_and_reports_unavailable_fill():
    sm = _sm()
    # very different images to force delta > threshold
    img_a = np.zeros((40, 200, 3), dtype=np.uint8)
    img_b = np.full((40, 200, 3), 255, dtype=np.uint8)
    ack, _, _ = _make_reader(sm, [img_a, img_b], [("anything", 0.0)])
    before = ack.capture_before("CANCEL_ALL")
    signal = ack.read_after("CANCEL_ALL", before)
    # cancel_all never has a fill_price
    assert signal.fill_price is None
    assert signal.fill_price_source == "unavailable"
    # and since pixels changed a lot, status is ok
    assert signal.status == "ok"


def test_screen_capture_is_thread_safe():
    """ScreenCapture instances must be usable from multiple threads —
    mss keeps Windows device contexts in thread-local storage and would
    raise '_thread._local' object has no attribute 'srcdc' otherwise."""
    import threading
    from app.capture.screen_capture import ScreenCapture
    from app.models.common import Region

    cap = ScreenCapture(monitor_index=1)
    region = Region(left=0, top=0, width=10, height=10)
    errors: list[Exception] = []

    def worker():
        try:
            cap.grab_region(region)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)

    # Any srcdc error would have been caught. Other errors (no monitor on
    # a CI box, permissions, etc.) we tolerate — the point is specifically
    # that the thread-local srcdc bug doesn't surface.
    assert not any("srcdc" in str(e) for e in errors), \
        f"mss thread-local crash resurfaced: {errors}"


def test_no_evidence_region_is_handled():
    sm = _sm(with_position=False)
    # no position_region and no status_region → read_after returns unknown
    sm = sm.model_copy(update={"position_region": None, "status_region": None})
    ack, _, _ = _make_reader(sm, [_blank(0)], [("x", 0.0)])
    before = ack.capture_before("BUY")
    assert before is None
    signal = ack.read_after("BUY", before)
    assert signal.status == "unknown"
    assert signal.fill_price is None
    assert signal.fill_price_source == "unavailable"
