"""
Replay runner for the signal engine.

Input formats:
  - JSONL of PriceTick rows, one per line
  - CSV with columns: ts_ms,price  (accepted ticks only)

The runner auto-confirms both entries and exits after emission so the state
machine can continue through multiple trades, and prints a concise summary.

Usage:
    python -m app.strategy.replay --jsonl runtime/ticks.jsonl
    python -m app.strategy.replay --csv  runtime/ticks.csv
    python -m app.strategy.replay --synth 300
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from app.capture.models import PriceTick
from app.models.config import SessionWindow, StrategyConfig, load_strategy_config
from app.utils import paths
from app.utils.logging_utils import setup_logging

from .engine import StrategyEngine
from .models import SignalIntent

log = logging.getLogger(__name__)


@dataclass
class ReplayStats:
    ticks: int = 0
    bars: int = 0
    entries: int = 0
    exits: int = 0
    cancel_alls: int = 0
    halts: int = 0
    intents: list[SignalIntent] = field(default_factory=list)


def iter_jsonl(path: Path) -> Iterator[PriceTick]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            yield PriceTick.model_validate(d)


def iter_csv(path: Path) -> Iterator[PriceTick]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield PriceTick(
                ts_ms=int(row["ts_ms"]),
                frame_id=int(row.get("frame_id", 0)),
                raw_text=row.get("raw_text", ""),
                price=float(row["price"]),
                confidence=float(row.get("confidence", 90.0)),
                accepted=True,
            )


def synthetic_ticks(n: int, seed: int = 1) -> Iterator[PriceTick]:
    rng = random.Random(seed)
    price = 19200.0
    ts = 1_700_000_000_000
    # deliberately create a sweep-and-reversal pattern near 19220.0 resistance
    for i in range(n):
        if i < 60:
            price += rng.choice([-0.25, 0.0, 0.25])
        elif 60 <= i < 70:
            price = 19220.0 + (i - 60) * 0.25  # push above resistance
        elif 70 <= i < 80:
            price = 19222.0 - (i - 70) * 0.5   # fail back below
        else:
            price += rng.choice([-0.25, 0.0, 0.25])
        ts += 1000
        # align
        price = round(price * 4) / 4
        yield PriceTick(
            ts_ms=ts, frame_id=i, raw_text=f"{price:.2f}", price=price,
            confidence=95.0, accepted=True,
        )


def run_replay(ticks: Iterable[PriceTick], cfg: StrategyConfig,
               always_in_session: bool = True) -> ReplayStats:
    stats = ReplayStats()
    intents: list[SignalIntent] = []

    # override session gate for replay unless the user opts out
    if always_in_session:
        cfg = cfg.model_copy(update={
            "session_windows": [SessionWindow(start="00:00", end="23:59", timezone="UTC")]
        })

    engine = StrategyEngine(cfg, emit=intents.append,
                            now_utc=lambda: datetime.now(tz=timezone.utc))

    for tick in ticks:
        stats.ticks += 1
        fresh = engine.on_tick(tick)
        # auto-confirm entries/exits so replay can cycle through trades
        for intent in fresh:
            if intent.action in ("BUY", "SELL"):
                engine.confirm_entry_filled(tick.price)
            elif intent.action in ("EXIT_LONG", "EXIT_SHORT"):
                pnl = _pnl_points(engine, intent)
                engine.confirm_exit_filled(realized_pnl_points=pnl)

    stats.bars = engine.debug.bars_seen
    stats.entries = engine.debug.entries
    stats.exits = engine.debug.exits
    stats.halts = engine.debug.halts
    stats.cancel_alls = sum(1 for i in intents if i.action == "CANCEL_ALL")
    stats.intents = intents
    return stats


def _pnl_points(engine: StrategyEngine, exit_intent: SignalIntent) -> float:
    pos = engine.state.position
    if not exit_intent.trigger_price or not pos.entry_price:
        return 0.0
    if exit_intent.action == "EXIT_LONG":
        return exit_intent.trigger_price - pos.entry_price
    return pos.entry_price - exit_intent.trigger_price


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--jsonl", type=Path)
    src.add_argument("--csv", type=Path)
    src.add_argument("--synth", type=int, help="replay N synthetic ticks")
    p.add_argument("--respect-session", action="store_true",
                   help="use the session window from strategy_config.json")
    args = p.parse_args(argv)

    cfg = load_strategy_config(paths.strategy_config_path())

    if args.jsonl:
        ticks = iter_jsonl(args.jsonl)
    elif args.csv:
        ticks = iter_csv(args.csv)
    else:
        ticks = synthetic_ticks(args.synth)

    stats = run_replay(ticks, cfg, always_in_session=not args.respect_session)

    log.info("=== Replay summary ===")
    log.info("  ticks:      %d", stats.ticks)
    log.info("  bars:       %d", stats.bars)
    log.info("  entries:    %d", stats.entries)
    log.info("  exits:      %d", stats.exits)
    log.info("  cancel_all: %d", stats.cancel_alls)
    log.info("  halts:      %d", stats.halts)
    for intent in stats.intents[:40]:
        log.info("  intent: %s  reason=%s  trigger=%s",
                 intent.action, intent.reason, intent.trigger_price)
    if len(stats.intents) > 40:
        log.info("  ... (%d more intents)", len(stats.intents) - 40)

    return 0


if __name__ == "__main__":
    sys.exit(main())
