"""
Visual overlay utility: capture current monitor, draw calibrated points/regions,
save preview image. Quick manual check that calibration still matches reality.

Usage:
    python -m app.execution.overlay
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.capture.screen_capture import ScreenCapture
from app.models.config import load_screen_map
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


def build_overlay(out_path: Path | None = None) -> Path:
    screen_map = load_screen_map(paths.screen_map_path())
    with ScreenCapture(screen_map.monitor_index) as cap:
        full = cap.grab_monitor()

    iu.draw_region(full, screen_map.tradovate_anchor_region.left,
                   screen_map.tradovate_anchor_region.top,
                   screen_map.tradovate_anchor_region.width,
                   screen_map.tradovate_anchor_region.height,
                   (0, 255, 255), "anchor")
    iu.draw_region(full, screen_map.price_region.left, screen_map.price_region.top,
                   screen_map.price_region.width, screen_map.price_region.height,
                   (0, 255, 0), "price")
    if screen_map.buy_point:
        iu.draw_point(full, screen_map.buy_point.x, screen_map.buy_point.y, (0, 180, 0), "buy")
    if screen_map.sell_point:
        iu.draw_point(full, screen_map.sell_point.x, screen_map.sell_point.y, (0, 0, 220), "sell")
    iu.draw_point(full, screen_map.cancel_all_point.x, screen_map.cancel_all_point.y,
                  (0, 140, 255), "cancel")
    if screen_map.position_region:
        r = screen_map.position_region
        iu.draw_region(full, r.left, r.top, r.width, r.height, (200, 200, 0), "position")
    if screen_map.status_region:
        r = screen_map.status_region
        iu.draw_region(full, r.left, r.top, r.width, r.height, (200, 0, 200), "status")

    out = out_path or (paths.screenshots_dir() / "execution_overlay.png")
    iu.save_png(full, out)
    return out


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    path = build_overlay(args.out)
    log.info("overlay saved: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
