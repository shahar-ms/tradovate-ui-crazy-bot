"""
Interactive screen calibration tool.

Flow:
  1. Pick a monitor (via mss).
  2. Capture the full monitor.
  3. Mark regions and points in a resizable OpenCV window:
       - anchor region (drag)
       - price region (drag)
       - buy point   (click)
       - sell point  (click)
       - cancel-all  (click)
       - (optional) position / status regions
  4. Review overlay.
  5. Save screen_map.json, anchor reference PNG, full screenshot, overlay preview.

Keybindings:
  Left mouse        : start/finish rectangle (for region steps) or click point (for point steps)
  r                 : redo current step
  s                 : skip current step (only allowed for optional steps)
  enter / space     : confirm and advance
  q / esc           : abort
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import mss
import numpy as np

from app.models.common import Point, Region, ScreenMap
from app.models.config import save_model_json
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


MAX_DISPLAY_WIDTH = 1600
MAX_DISPLAY_HEIGHT = 900


@dataclass
class StepResult:
    region: Optional[Region] = None
    point: Optional[Point] = None
    skipped: bool = False


@dataclass
class CalibrationResult:
    monitor_index: int
    screen_width: int
    screen_height: int
    full_image: np.ndarray
    anchor_region: Region
    price_region: Region
    buy_point: Point
    sell_point: Point
    cancel_all_point: Point
    position_region: Optional[Region] = None
    status_region: Optional[Region] = None
    optional_overlays: dict = field(default_factory=dict)


# ------------------------------- monitor selection ------------------------------- #

def list_monitors() -> list[dict]:
    with mss.mss() as sct:
        # mss.monitors[0] is a "virtual union" of all monitors, indices 1..N are physical.
        return list(sct.monitors)


def prompt_monitor_choice() -> int:
    monitors = list_monitors()
    if len(monitors) < 2:
        raise RuntimeError("No physical monitors detected by mss.")
    print("\nDetected monitors (index 0 is the virtual all-monitor union):")
    for i, m in enumerate(monitors):
        tag = " [VIRTUAL]" if i == 0 else ""
        print(f"  [{i}] {m['width']}x{m['height']} @ ({m['left']},{m['top']}){tag}")
    while True:
        raw = input(f"Select monitor index (1..{len(monitors) - 1}): ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("  Not a number, try again.")
            continue
        if 1 <= idx <= len(monitors) - 1:
            return idx
        print("  Out of range, try again.")


def capture_monitor(monitor_index: int) -> tuple[np.ndarray, dict]:
    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]
        raw = np.array(sct.grab(monitor))  # BGRA
        bgr = iu.bgra_to_bgr(raw)
        return bgr, monitor


# ----------------------------- interactive window UI ----------------------------- #

class InteractiveCanvas:
    """
    Shows a (possibly downscaled) preview of the full monitor image and lets the user
    mark either a rectangle or a point. All returned coordinates are in the original,
    full-resolution image space (not the preview space).
    """

    def __init__(self, full_image: np.ndarray, window_title: str = "Tradovate calibrator"):
        self.full_image = full_image
        self.window_title = window_title
        h, w = full_image.shape[:2]
        scale_w = MAX_DISPLAY_WIDTH / w
        scale_h = MAX_DISPLAY_HEIGHT / h
        self.scale = min(1.0, scale_w, scale_h)
        self.disp_w = int(w * self.scale)
        self.disp_h = int(h * self.scale)
        self._preview = cv2.resize(full_image, (self.disp_w, self.disp_h),
                                   interpolation=cv2.INTER_AREA)
        self._mode: str = "idle"  # "region" | "point"
        self._drag_start: tuple[int, int] | None = None
        self._drag_end: tuple[int, int] | None = None
        self._point: tuple[int, int] | None = None
        self._status_text: str = ""

    def _to_full(self, xy: tuple[int, int]) -> tuple[int, int]:
        x, y = xy
        return int(round(x / self.scale)), int(round(y / self.scale))

    def _render_frame(self, extra_overlays: list[tuple] | None = None) -> np.ndarray:
        img = self._preview.copy()
        if self._mode == "region" and self._drag_start and self._drag_end:
            cv2.rectangle(img, self._drag_start, self._drag_end, (0, 255, 0), 2)
        if self._mode == "point" and self._point:
            cv2.circle(img, self._point, 10, (0, 0, 255), 2)
            cv2.circle(img, self._point, 2, (0, 0, 255), -1)
        if extra_overlays:
            for ov in extra_overlays:
                kind = ov[0]
                if kind == "region":
                    _, left, top, width, height, color, label = ov
                    l = int(round(left * self.scale))
                    t = int(round(top * self.scale))
                    w = int(round(width * self.scale))
                    h = int(round(height * self.scale))
                    cv2.rectangle(img, (l, t), (l + w, t + h), color, 2)
                    if label:
                        cv2.putText(img, label, (l, max(0, t - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
                elif kind == "point":
                    _, x, y, color, label = ov
                    sx = int(round(x * self.scale))
                    sy = int(round(y * self.scale))
                    cv2.circle(img, (sx, sy), 10, color, 2)
                    cv2.circle(img, (sx, sy), 2, color, -1)
                    if label:
                        cv2.putText(img, label, (sx + 12, sy - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        if self._status_text:
            cv2.rectangle(img, (0, 0), (img.shape[1], 32), (0, 0, 0), -1)
            cv2.putText(img, self._status_text, (8, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        return img

    def _mouse_region(self, event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            self._drag_start = (x, y)
            self._drag_end = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self._drag_start is not None and (flags & cv2.EVENT_FLAG_LBUTTON):
            self._drag_end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self._drag_start is not None:
            self._drag_end = (x, y)

    def _mouse_point(self, event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            self._point = (x, y)

    def ask_region(self, label: str, required: bool,
                   existing_overlays: list[tuple] | None = None) -> StepResult:
        self._mode = "region"
        self._drag_start = None
        self._drag_end = None
        hint = " (s=skip)" if not required else ""
        self._status_text = f"Drag {label}. Enter=confirm, r=redo, q=abort{hint}"
        cv2.namedWindow(self.window_title, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.window_title, self._mouse_region)
        while True:
            cv2.imshow(self.window_title, self._render_frame(existing_overlays))
            key = cv2.waitKey(20) & 0xFF
            if key in (13, 32):  # enter / space
                if self._drag_start and self._drag_end:
                    x1, y1 = self._drag_start
                    x2, y2 = self._drag_end
                    fx1, fy1 = self._to_full((min(x1, x2), min(y1, y2)))
                    fx2, fy2 = self._to_full((max(x1, x2), max(y1, y2)))
                    w = max(1, fx2 - fx1)
                    h = max(1, fy2 - fy1)
                    return StepResult(region=Region(left=fx1, top=fy1, width=w, height=h))
            elif key == ord("r"):
                self._drag_start = None
                self._drag_end = None
            elif key == ord("s") and not required:
                return StepResult(skipped=True)
            elif key in (ord("q"), 27):
                raise KeyboardInterrupt("User aborted calibration.")

    def ask_point(self, label: str, required: bool,
                  existing_overlays: list[tuple] | None = None) -> StepResult:
        self._mode = "point"
        self._point = None
        hint = " (s=skip)" if not required else ""
        self._status_text = f"Click {label}. Enter=confirm, r=redo, q=abort{hint}"
        cv2.namedWindow(self.window_title, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.window_title, self._mouse_point)
        while True:
            cv2.imshow(self.window_title, self._render_frame(existing_overlays))
            key = cv2.waitKey(20) & 0xFF
            if key in (13, 32):
                if self._point is not None:
                    fx, fy = self._to_full(self._point)
                    return StepResult(point=Point(x=fx, y=fy))
            elif key == ord("r"):
                self._point = None
            elif key == ord("s") and not required:
                return StepResult(skipped=True)
            elif key in (ord("q"), 27):
                raise KeyboardInterrupt("User aborted calibration.")

    def review(self, overlays: list[tuple]) -> str:
        """Show final overlay and let user choose: s=save, r=redo all, q=abort."""
        self._mode = "idle"
        self._drag_start = None
        self._drag_end = None
        self._point = None
        self._status_text = "Review: s=save, r=restart, q=abort"
        cv2.namedWindow(self.window_title, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.window_title, lambda *a, **k: None)
        while True:
            cv2.imshow(self.window_title, self._render_frame(overlays))
            key = cv2.waitKey(20) & 0xFF
            if key == ord("s"):
                return "save"
            if key == ord("r"):
                return "redo"
            if key in (ord("q"), 27):
                return "abort"

    def close(self) -> None:
        cv2.destroyWindow(self.window_title)


# ------------------------------- calibration flow -------------------------------- #

def run_calibration_flow() -> CalibrationResult:
    monitor_index = prompt_monitor_choice()
    full_image, monitor_info = capture_monitor(monitor_index)
    screen_w = int(monitor_info["width"])
    screen_h = int(monitor_info["height"])
    log.info("Captured monitor %d: %dx%d", monitor_index, screen_w, screen_h)

    canvas = InteractiveCanvas(full_image)

    while True:
        overlays: list[tuple] = []

        anchor = canvas.ask_region("Tradovate anchor region", required=True, existing_overlays=overlays)
        overlays.append(("region", anchor.region.left, anchor.region.top,
                         anchor.region.width, anchor.region.height,
                         (0, 255, 255), "anchor"))

        price = canvas.ask_region("price region", required=True, existing_overlays=overlays)
        overlays.append(("region", price.region.left, price.region.top,
                         price.region.width, price.region.height,
                         (0, 255, 0), "price"))

        buy = canvas.ask_point("BUY button", required=True, existing_overlays=overlays)
        overlays.append(("point", buy.point.x, buy.point.y, (0, 180, 0), "buy"))

        sell = canvas.ask_point("SELL button", required=True, existing_overlays=overlays)
        overlays.append(("point", sell.point.x, sell.point.y, (0, 0, 220), "sell"))

        cancel = canvas.ask_point("CANCEL-ALL button", required=True, existing_overlays=overlays)
        overlays.append(("point", cancel.point.x, cancel.point.y, (0, 140, 255), "cancel"))

        pos_res = canvas.ask_region("(optional) position region [s to skip]",
                                    required=False, existing_overlays=overlays)
        if pos_res.region:
            overlays.append(("region", pos_res.region.left, pos_res.region.top,
                             pos_res.region.width, pos_res.region.height,
                             (200, 200, 0), "position"))

        stat_res = canvas.ask_region("(optional) status region [s to skip]",
                                     required=False, existing_overlays=overlays)
        if stat_res.region:
            overlays.append(("region", stat_res.region.left, stat_res.region.top,
                             stat_res.region.width, stat_res.region.height,
                             (200, 0, 200), "status"))

        choice = canvas.review(overlays)
        if choice == "save":
            canvas.close()
            return CalibrationResult(
                monitor_index=monitor_index,
                screen_width=screen_w,
                screen_height=screen_h,
                full_image=full_image,
                anchor_region=anchor.region,
                price_region=price.region,
                buy_point=buy.point,
                sell_point=sell.point,
                cancel_all_point=cancel.point,
                position_region=pos_res.region,
                status_region=stat_res.region,
            )
        if choice == "abort":
            canvas.close()
            raise KeyboardInterrupt("User aborted at review.")
        # otherwise redo


# --------------------------------- persistence ----------------------------------- #

def persist_calibration(result: CalibrationResult) -> ScreenMap:
    iu.save_png(result.full_image, paths.calibration_full_path())

    anchor_crop = iu.crop(
        result.full_image,
        result.anchor_region.left,
        result.anchor_region.top,
        result.anchor_region.width,
        result.anchor_region.height,
    )
    iu.save_png(anchor_crop, paths.anchor_reference_path())

    price_crop = iu.crop(
        result.full_image,
        result.price_region.left,
        result.price_region.top,
        result.price_region.width,
        result.price_region.height,
    )
    iu.save_png(price_crop, paths.screenshots_dir() / "price_region_reference.png")

    overlay = result.full_image.copy()
    iu.draw_region(overlay, result.anchor_region.left, result.anchor_region.top,
                   result.anchor_region.width, result.anchor_region.height,
                   color=(0, 255, 255), label="anchor")
    iu.draw_region(overlay, result.price_region.left, result.price_region.top,
                   result.price_region.width, result.price_region.height,
                   color=(0, 255, 0), label="price")
    iu.draw_point(overlay, result.buy_point.x, result.buy_point.y, (0, 180, 0), "buy")
    iu.draw_point(overlay, result.sell_point.x, result.sell_point.y, (0, 0, 220), "sell")
    iu.draw_point(overlay, result.cancel_all_point.x, result.cancel_all_point.y, (0, 140, 255), "cancel")
    if result.position_region:
        iu.draw_region(overlay, result.position_region.left, result.position_region.top,
                       result.position_region.width, result.position_region.height,
                       color=(200, 200, 0), label="position")
    if result.status_region:
        iu.draw_region(overlay, result.status_region.left, result.status_region.top,
                       result.status_region.width, result.status_region.height,
                       color=(200, 0, 200), label="status")
    iu.save_png(overlay, paths.calibration_overlay_path())

    screen_map = ScreenMap(
        monitor_index=result.monitor_index,
        screen_width=result.screen_width,
        screen_height=result.screen_height,
        browser_name="chrome",
        tradovate_anchor_region=result.anchor_region,
        tradovate_anchor_reference_path=str(paths.anchor_reference_path()
                                           .relative_to(paths.project_root())).replace("\\", "/"),
        price_region=result.price_region,
        buy_point=result.buy_point,
        sell_point=result.sell_point,
        cancel_all_point=result.cancel_all_point,
        position_region=result.position_region,
        status_region=result.status_region,
    )
    save_model_json(screen_map, paths.screen_map_path())
    return screen_map


# ------------------------------------- main -------------------------------------- #

def main() -> int:
    setup_logging()
    try:
        result = run_calibration_flow()
    except KeyboardInterrupt as e:
        log.warning("Calibration aborted: %s", e)
        return 2

    screen_map = persist_calibration(result)
    log.info("Calibration saved to %s", paths.screen_map_path())

    # Validate immediately
    try:
        from app.calibration.validator import validate_calibration
        report = validate_calibration()
        for line in report.lines:
            print(line)
        return 0 if report.ready else 1
    except Exception as exc:
        log.exception("Post-save validation failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
