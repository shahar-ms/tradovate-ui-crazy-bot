"""
Explicit strategy state machine. Prevents accidental double-entry or
ambiguous transitions.

States: FLAT, PENDING_ENTRY, LONG, SHORT, PENDING_EXIT, HALTED
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import PositionState, SignalActionT, StrategyStateT

log = logging.getLogger(__name__)


class InvalidTransition(RuntimeError):
    pass


class StrategyStateMachine:
    def __init__(self):
        self.state: StrategyStateT = "FLAT"
        self.position = PositionState(side="flat")
        self.halt_reason: Optional[str] = None

    # ---- predicates ---- #
    def is_flat(self) -> bool:
        return self.state == "FLAT"

    def is_in_position(self) -> bool:
        return self.state in ("LONG", "SHORT")

    def is_long(self) -> bool:
        return self.state == "LONG"

    def is_short(self) -> bool:
        return self.state == "SHORT"

    def is_halted(self) -> bool:
        return self.state == "HALTED"

    def is_pending(self) -> bool:
        return self.state in ("PENDING_ENTRY", "PENDING_EXIT")

    # ---- transitions ---- #
    def to_pending_entry(self, action: SignalActionT, trigger_price: float,
                         stop: float, target: float) -> None:
        if self.state != "FLAT":
            raise InvalidTransition(f"cannot enter from state {self.state}")
        if action not in ("BUY", "SELL"):
            raise InvalidTransition(f"not an entry action: {action}")
        self.state = "PENDING_ENTRY"
        self.position = PositionState(
            side="long" if action == "BUY" else "short",
            entry_price=trigger_price,
            stop_price=stop,
            target_price=target,
            bars_in_trade=0,
        )

    def confirm_entry(self, fill_price: Optional[float] = None) -> None:
        if self.state != "PENDING_ENTRY":
            raise InvalidTransition(f"confirm_entry from {self.state}")
        if fill_price is not None:
            self.position.entry_price = fill_price
        self.state = "LONG" if self.position.side == "long" else "SHORT"

    def reject_entry(self, reason: str) -> None:
        if self.state != "PENDING_ENTRY":
            raise InvalidTransition(f"reject_entry from {self.state}")
        log.warning("entry rejected: %s", reason)
        self.state = "FLAT"
        self.position = PositionState(side="flat")

    def to_pending_exit(self) -> None:
        if self.state not in ("LONG", "SHORT"):
            raise InvalidTransition(f"exit from {self.state}")
        self.state = "PENDING_EXIT"

    def confirm_exit(self) -> None:
        if self.state != "PENDING_EXIT":
            raise InvalidTransition(f"confirm_exit from {self.state}")
        self.state = "FLAT"
        self.position = PositionState(side="flat")

    def halt(self, reason: str) -> None:
        log.error("strategy HALTED: %s", reason)
        self.state = "HALTED"
        self.halt_reason = reason

    def resume(self) -> None:
        if self.state != "HALTED":
            return
        log.warning("strategy RESUMED from HALT")
        self.state = "FLAT"
        self.halt_reason = None
        self.position = PositionState(side="flat")

    # ---- lifecycle ---- #
    def on_bar_close(self) -> None:
        if self.state in ("LONG", "SHORT"):
            self.position.bars_in_trade += 1
