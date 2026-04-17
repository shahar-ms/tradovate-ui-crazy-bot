"""
Debug / replay utilities for the price stream.

Usage:
    # live debug: run the price stream and print accepted ticks
    python -m app.capture.debug_tools live --seconds 30

    # replay saved PNGs and compute acceptance statistics per recipe
    python -m app.capture.debug_tools replay --dir runtime/screenshots/debug_price
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from app.capture.ocr_reader import build_reader
from app.capture.preprocess import make_variants
from app.capture.parser import parse_price
from app.capture.price_stream import PriceStream
from app.capture.validator import PriceValidator
from app.models.config import load_bot_config, load_screen_map
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


def run_live(seconds: int) -> int:
    bot_cfg = load_bot_config(paths.bot_config_path())
    screen_map = load_screen_map(paths.screen_map_path())

    stream = PriceStream(
        region=screen_map.price_region,
        monitor_index=screen_map.monitor_index,
        bot_cfg=bot_cfg,
    )
    stream.start()
    try:
        deadline = time.time() + seconds
        last_print = 0.0
        while time.time() < deadline:
            time.sleep(0.2)
            tick = stream.get_latest_tick()
            if tick and time.time() - last_print > 0.5:
                state = stream.get_health().health_state
                if tick.accepted:
                    log.info("tick #%d price=%.2f conf=%.1f recipe=%s health=%s",
                             tick.frame_id, tick.price, tick.confidence, tick.recipe, state)
                else:
                    log.info("reject #%d raw=%r reason=%s health=%s",
                             tick.frame_id, tick.raw_text, tick.reject_reason, state)
                last_print = time.time()
    finally:
        stream.stop()
    return 0


def run_replay(directory: Path, out_csv: Path | None = None) -> int:
    bot_cfg = load_bot_config(paths.bot_config_path())
    reader = build_reader(bot_cfg.ocr_backend)
    validator = PriceValidator(
        min_confidence=bot_cfg.min_ocr_confidence,
        max_jump_points=bot_cfg.max_jump_points,
    )

    files = sorted(p for p in directory.glob("*.png"))
    if not files:
        log.error("no PNG files in %s", directory)
        return 1

    stats_accepted_by_recipe: Counter[str] = Counter()
    stats_rejected_by_recipe: Counter[str] = Counter()
    rows: list[dict] = []
    prev: float | None = None

    for path in files:
        img = iu.load_png(path)
        variants = make_variants(img, bot_cfg.preprocess_recipes)
        file_row = {"file": str(path.name)}
        any_accepted = False
        for recipe_name, v in variants.items():
            ocr = reader.read(v)
            parsed = parse_price(ocr.raw_text)
            verdict = validator.check(parsed.value, ocr.confidence, prev)
            file_row[recipe_name] = json.dumps({
                "raw": ocr.raw_text,
                "conf": round(ocr.confidence, 1),
                "parsed": parsed.value,
                "accepted": verdict.accepted,
                "reason": verdict.reason,
            })
            if verdict.accepted:
                stats_accepted_by_recipe[recipe_name] += 1
                any_accepted = True
                if verdict.value is not None:
                    prev = verdict.value
            else:
                stats_rejected_by_recipe[recipe_name] += 1
        file_row["file_accepted"] = any_accepted
        rows.append(file_row)

    log.info("=== Replay stats (%d files) ===", len(files))
    for name in bot_cfg.preprocess_recipes:
        a = stats_accepted_by_recipe[name]
        r = stats_rejected_by_recipe[name]
        total = a + r
        rate = (a / total * 100.0) if total else 0.0
        log.info("  %-25s accepted=%4d  rejected=%4d  rate=%.1f%%",
                 name, a, r, rate)

    if out_csv:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for row in rows for k in row.keys()}))
            w.writeheader()
            for row in rows:
                w.writerow(row)
        log.info("wrote %s", out_csv)

    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("live")
    pl.add_argument("--seconds", type=int, default=30)

    pr = sub.add_parser("replay")
    pr.add_argument("--dir", type=Path, required=True)
    pr.add_argument("--csv", type=Path, default=None)

    args = p.parse_args(argv)
    if args.cmd == "live":
        return run_live(args.seconds)
    if args.cmd == "replay":
        return run_replay(args.dir, args.csv)
    return 2


if __name__ == "__main__":
    sys.exit(main())
