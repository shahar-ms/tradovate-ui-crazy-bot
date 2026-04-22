"""
Validates that calibration artifacts exist and are usable by later components.

Checks:
  1. screen_map.json exists and parses
  2. chosen monitor exists via mss (skipped in --offline mode)
  3. all regions within monitor bounds
  4. all points within monitor bounds
  5. anchor reference image exists and loads
  6. current anchor crop vs reference has similarity >= threshold
     (skipped in --offline mode)
  7. price region non-empty

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import mss
import numpy as np

from app.models.common import Point, Region, ScreenMap
from app.models.config import ConfigError, load_bot_config, load_screen_map
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    lines: list[str] = field(default_factory=list)
    ready: bool = True

    def ok(self, msg: str) -> None:
        self.lines.append(f"[OK]   {msg}")

    def warn(self, msg: str) -> None:
        self.lines.append(f"[WARN] {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"[FAIL] {msg}")
        self.ready = False


def _region_in_bounds(region: Region, w: int, h: int) -> bool:
    return (
        region.left >= 0
        and region.top >= 0
        and region.left + region.width <= w
        and region.top + region.height <= h
        and region.width > 0
        and region.height > 0
    )


def _point_in_bounds(point: Point, w: int, h: int) -> bool:
    return 0 <= point.x < w and 0 <= point.y < h


def _capture_region(monitor_index: int, region: Region) -> np.ndarray:
    with mss.mss() as sct:
        mon = sct.monitors[monitor_index]
        grab = {
            "left": mon["left"] + region.left,
            "top": mon["top"] + region.top,
            "width": region.width,
            "height": region.height,
        }
        raw = np.array(sct.grab(grab))
        return iu.bgra_to_bgr(raw)


def validate_calibration(offline: bool = False) -> ValidationReport:
    report = ValidationReport()

    # 1. screen_map.json
    sm_path = paths.screen_map_path()
    if not sm_path.exists():
        report.fail(f"screen_map.json not found at {sm_path}")
        return report
    try:
        screen_map: ScreenMap = load_screen_map(sm_path)
    except ConfigError as e:
        report.fail(f"screen_map.json invalid: {e}")
        return report
    report.ok(f"screen_map.json loaded ({sm_path})")

    # bot_config exists
    try:
        bot_cfg = load_bot_config(paths.bot_config_path())
        anchor_threshold = bot_cfg.anchor_match_threshold
        report.ok(f"bot_config.json loaded (anchor_threshold={anchor_threshold:.2f})")
    except ConfigError as e:
        report.fail(f"bot_config.json invalid: {e}")
        return report

    # 2. monitor available
    if not offline:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
            if screen_map.monitor_index >= len(monitors) or screen_map.monitor_index < 1:
                report.fail(f"monitor_index {screen_map.monitor_index} not available "
                            f"(found {len(monitors) - 1} physical monitors)")
                return report
            live_mon = monitors[screen_map.monitor_index]
            if live_mon["width"] != screen_map.screen_width or live_mon["height"] != screen_map.screen_height:
                report.fail(
                    f"monitor {screen_map.monitor_index} size changed "
                    f"({live_mon['width']}x{live_mon['height']} vs calibrated "
                    f"{screen_map.screen_width}x{screen_map.screen_height})"
                )
                return report
            report.ok(f"monitor {screen_map.monitor_index} available "
                      f"({live_mon['width']}x{live_mon['height']})")
        except Exception as e:
            report.fail(f"monitor detection failed: {e}")
            return report
    else:
        report.ok("monitor check skipped (offline mode)")

    w, h = screen_map.screen_width, screen_map.screen_height

    # 3. regions within bounds
    region_checks = {
        "anchor_region": screen_map.tradovate_anchor_region,
        "price_region": screen_map.price_region,
    }
    if screen_map.position_region:
        region_checks["position_region"] = screen_map.position_region
    if screen_map.status_region:
        region_checks["status_region"] = screen_map.status_region
    for name, reg in region_checks.items():
        if _region_in_bounds(reg, w, h):
            report.ok(f"{name} within bounds ({reg.width}x{reg.height} @ "
                      f"{reg.left},{reg.top})")
        else:
            report.fail(f"{name} out of bounds: {reg.model_dump()}")

    # 4. points within bounds
    point_checks = {
        "buy_point": screen_map.buy_point,
        "sell_point": screen_map.sell_point,
        "cancel_all_point": screen_map.cancel_all_point,
    }
    for name, pt in point_checks.items():
        if _point_in_bounds(pt, w, h):
            report.ok(f"{name} within bounds ({pt.x},{pt.y})")
        else:
            report.fail(f"{name} out of bounds ({pt.x},{pt.y})")

    # 5. anchor reference image exists
    anchor_ref_path = paths.resolve_relative(screen_map.tradovate_anchor_reference_path)
    if not anchor_ref_path.exists():
        report.fail(f"anchor reference image not found: {anchor_ref_path}")
        return report
    try:
        anchor_ref = iu.load_png(anchor_ref_path)
        report.ok(f"anchor reference loaded ({anchor_ref.shape[1]}x{anchor_ref.shape[0]})")
    except Exception as e:
        report.fail(f"anchor reference could not be read: {e}")
        return report

    # 6. similarity against live anchor
    if not offline:
        try:
            live_anchor = _capture_region(screen_map.monitor_index, screen_map.tradovate_anchor_region)
            sim = iu.similarity_score(anchor_ref, live_anchor)
            if sim >= anchor_threshold:
                report.ok(f"anchor similarity {sim:.3f} >= threshold {anchor_threshold:.2f}")
            else:
                report.fail(f"anchor similarity {sim:.3f} < threshold {anchor_threshold:.2f}")
        except Exception as e:
            report.fail(f"live anchor capture failed: {e}")
    else:
        report.ok("anchor similarity check skipped (offline mode)")

    # 7. price region non-empty
    pr = screen_map.price_region
    if pr.width >= 10 and pr.height >= 8:
        report.ok(f"price region dimensions ok ({pr.width}x{pr.height})")
    else:
        report.warn(f"price region looks small ({pr.width}x{pr.height}); OCR may suffer")

    # regenerate overlay (always safe)
    try:
        full_path = paths.calibration_full_path()
        if full_path.exists():
            full = iu.load_png(full_path)
            _draw_overlay(full, screen_map)
            iu.save_png(full, paths.calibration_overlay_path())
            report.ok(f"overlay preview refreshed: {paths.calibration_overlay_path()}")
    except Exception as e:
        report.warn(f"overlay refresh skipped: {e}")

    report.lines.append("")
    report.lines.append(f"READY_FOR_FILE_02 = {str(report.ready).lower()}")
    return report


def _draw_overlay(img: np.ndarray, sm: ScreenMap) -> None:
    iu.draw_region(img, sm.tradovate_anchor_region.left, sm.tradovate_anchor_region.top,
                   sm.tradovate_anchor_region.width, sm.tradovate_anchor_region.height,
                   (0, 255, 255), "anchor")
    iu.draw_region(img, sm.price_region.left, sm.price_region.top,
                   sm.price_region.width, sm.price_region.height,
                   (0, 255, 0), "price")
    iu.draw_point(img, sm.buy_point.x, sm.buy_point.y, (0, 180, 0), "buy")
    iu.draw_point(img, sm.sell_point.x, sm.sell_point.y, (0, 0, 220), "sell")
    iu.draw_point(img, sm.cancel_all_point.x, sm.cancel_all_point.y, (0, 140, 255), "cancel")
    if sm.position_region:
        iu.draw_region(img, sm.position_region.left, sm.position_region.top,
                       sm.position_region.width, sm.position_region.height,
                       (200, 200, 0), "position")
    size_region = getattr(sm, "position_size_region", None)
    if size_region:
        iu.draw_region(img, size_region.left, size_region.top,
                       size_region.width, size_region.height,
                       (0, 200, 255), "pos_size")
    if sm.status_region:
        iu.draw_region(img, sm.status_region.left, sm.status_region.top,
                       sm.status_region.width, sm.status_region.height,
                       (200, 0, 200), "status")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true",
                        help="Skip live monitor/anchor checks (for CI/testing).")
    args = parser.parse_args(argv)

    setup_logging()
    report = validate_calibration(offline=args.offline)
    for line in report.lines:
        print(line)
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
