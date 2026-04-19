"""
Signal engine: consumes PriceTick objects, emits SignalIntent objects.

Pipeline per tick:
    1. (optional) check exits on the raw tick (stop/target hit intra-bar)
    2. feed tick to bar builder
    3. on a newly closed bar:
        a. update levels
        b. on_bar for risk + state machine
        c. check time-stop exits
        d. check entry pattern (only if FLAT and risk.can_enter)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from app.capture.models import PriceTick
from app.models.config import StrategyConfig
from app.utils.time_utils import now_ms

from .bar_builder import BarBuilder
from .levels import LevelDetector, LevelDetectorConfig
from .models import MicroBar, SignalActionT, SignalIntent
from .risk_manager import RiskManager
from .signal_rules import SweepConfig, SweepSignalEngine
from .state_machine import StrategyStateMachine

log = logging.getLogger(__name__)


@dataclass
class EngineDebug:
    bars_seen: int = 0
    entries: int = 0
    exits: int = 0
    halts: int = 0


class StrategyEngine:
    """
    High-level strategy controller.

    Inputs:
        - PriceTick objects via on_tick()
        - health ok/not via set_price_stream_ok()

    Outputs:
        - SignalIntent objects published via emit() callback or returned from on_tick()
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        emit: Optional[Callable[[SignalIntent], None]] = None,
        now_utc: Optional[Callable[[], datetime]] = None,
    ):
        self.cfg = cfg
        self._emit_cb = emit
        self._now_utc = now_utc or (lambda: datetime.now(tz=timezone.utc))

        self.bars = BarBuilder(bar_seconds=cfg.bar_seconds)
        self.levels = LevelDetector(LevelDetectorConfig(
            lookback_bars=cfg.level_lookback_bars,
            tolerance_points=cfg.level_touch_tolerance_points,
            min_touches=cfg.min_touches_for_level,
        ))
        self.rules = SweepSignalEngine(SweepConfig(
            break_distance_points=cfg.sweep_break_distance_points,
            return_timeout_bars=cfg.sweep_return_timeout_bars,
        ))
        self.state = StrategyStateMachine()
        self.risk = RiskManager(cfg)

        self._price_stream_ok: bool = True
        self.debug = EngineDebug()
        self._last_accepted_price: Optional[float] = None
        # Guards state transitions when both the bar-driven path and manual
        # HUD clicks can arrive concurrently.
        self._transition_lock = threading.Lock()
        # When False the engine still updates bars/levels/state for bookkeeping
        # but will NOT emit auto entry or exit intents. Manual submissions via
        # submit_manual_intent are unaffected. Operator toggles this from the
        # HUD "Enable/Disable Bot" control.
        self.auto_enabled: bool = True
        self._pending_exit_action: Optional[SignalActionT] = None

    # ---- externally driven state ---- #

    def set_price_stream_ok(self, ok: bool) -> None:
        self._price_stream_ok = ok

    def halt(self, reason: str) -> None:
        self.state.halt(reason)
        self.debug.halts += 1

    def on_execution_ack_unknown(self) -> None:
        """Halt because the execution adapter could not confirm success."""
        self.halt("execution_ack_unknown")

    # ---- manual intents (HUD-originated) ---- #

    def submit_manual_intent(self, action: SignalActionT,
                             reason: str = "manual_hud"
                             ) -> tuple[bool, str, list[SignalIntent]]:
        """
        Route a HUD-originated click through the strategy state machine.

        Accepts: BUY / SELL (entries), CANCEL_ALL (any time), EXIT_LONG / EXIT_SHORT
        (only while LONG / SHORT respectively). Rejects silently-dangerous
        combinations (buy while long, sell while short, etc) so we never
        implicitly flip or pyramid a position.

        Returns (accepted, message, intents). On accept, `intents` is the
        ordered list of SignalIntent objects the caller must forward to the
        execution bus — the engine intentionally does NOT publish them
        itself because the bar-driven path uses the same _build_intent
        mechanism and we don't want to double-publish. The controller owns
        the bus.
        """
        with self._transition_lock:
            if self.state.is_halted():
                return False, "halted", []

            price = self._last_accepted_price
            out: list[SignalIntent] = []

            if action == "CANCEL_ALL":
                out.append(self._build_intent("CANCEL_ALL", reason, price))
                return True, "emitted", out

            if action in ("BUY", "SELL"):
                if not self.state.is_flat():
                    return False, "position active \u2014 use Cancel All first", []
                if not self._price_stream_ok:
                    return False, "price stream not ok", []
                decision = self.risk.can_enter(self._now_utc(), self._price_stream_ok)
                if not decision.can_enter:
                    return False, decision.reason or "risk blocked", []
                if price is None:
                    return False, "no price yet", []
                out.extend(self._initiate_entry(action, trigger_price=price, reason=reason))
                return True, "emitted", out

            if action == "EXIT_LONG":
                if not self.state.is_long():
                    return False, "not in a long position", []
                out.append(self._build_exit(reason))
                return True, "emitted", out

            if action == "EXIT_SHORT":
                if not self.state.is_short():
                    return False, "not in a short position", []
                out.append(self._build_exit(reason))
                return True, "emitted", out

            return False, f"unsupported manual action: {action}", []

    # ---- tick handling ---- #

    def on_tick(self, tick: PriceTick) -> list[SignalIntent]:
        out: list[SignalIntent] = []
        if not tick.accepted or tick.price is None:
            return out

        self._last_accepted_price = tick.price

        # 1. intra-bar exits on the raw tick
        if self.state.is_in_position():
            exit_sig = self._check_exit_on_tick(tick.price)
            if exit_sig:
                out.append(exit_sig)
                return out  # one action per call

        # 2. feed bar builder
        closed_bar = self.bars.on_tick(tick.ts_ms, tick.price)
        if closed_bar is None:
            return out

        # 3. on bar close
        out.extend(self._on_bar_close(closed_bar))
        return out

    # ---- bar close pipeline ---- #

    def _on_bar_close(self, bar: MicroBar) -> list[SignalIntent]:
        self.debug.bars_seen += 1
        self.levels.on_bar(bar)
        self.risk.on_bar()
        self.state.on_bar_close()

        out: list[SignalIntent] = []

        # Bookkeeping above always runs so position tracking + level detection
        # stay correct when the operator re-enables auto. But when auto is
        # disabled, we don't emit any auto entry or exit intents.
        if not self.auto_enabled:
            return out

        # time-stop exit
        if self.state.is_in_position():
            pos = self.state.position
            if pos.bars_in_trade >= self.cfg.time_stop_bars:
                out.append(self._build_exit("time_stop"))
                return out

        # entry check (only when flat)
        if self.state.is_flat() and not self.state.is_halted():
            decision = self.risk.can_enter(self._now_utc(), self._price_stream_ok)
            if decision.can_enter:
                sig = self.rules.on_bar(bar, self.levels)
                if sig is not None:
                    out.extend(self._initiate_entry(sig.action, sig.trigger_price, sig.reason))
            else:
                # consume candidates so we don't later accidentally trigger an old one
                # we still call rules.on_bar to update its internal pending state
                _ = self.rules.on_bar(bar, self.levels)
        return out

    # ---- entries ---- #

    def _initiate_entry(self, action: SignalActionT, trigger_price: float, reason: str) -> list[SignalIntent]:
        if action == "BUY":
            stop = trigger_price - self.cfg.stop_loss_points
            target = trigger_price + self.cfg.take_profit_points
        elif action == "SELL":
            stop = trigger_price + self.cfg.stop_loss_points
            target = trigger_price - self.cfg.take_profit_points
        else:
            return []

        self.state.to_pending_entry(action, trigger_price, stop, target)
        self.debug.entries += 1
        out: list[SignalIntent] = []
        if self.cfg.cancel_all_before_new_entry:
            out.append(self._build_intent("CANCEL_ALL", f"cleanup_before_{action.lower()}",
                                          trigger_price))
        out.append(self._build_intent(action, reason, trigger_price))
        return out

    def confirm_entry_filled(self, fill_price: Optional[float] = None) -> None:
        """Called by the orchestrator when the execution layer acks the entry."""
        if self.state.is_pending():
            self.state.confirm_entry(fill_price)
            self.risk.on_entry()

    def reject_entry(self, reason: str) -> None:
        if self.state.state == "PENDING_ENTRY":
            self.state.reject_entry(reason)

    # ---- exits ---- #

    def _check_exit_on_tick(self, price: float) -> Optional[SignalIntent]:
        # Auto exits (stop/target) only fire when auto trading is enabled.
        if not self.auto_enabled:
            return None
        pos = self.state.position
        if pos.side == "long":
            if pos.stop_price is not None and price <= pos.stop_price:
                return self._build_exit("stop_loss")
            if pos.target_price is not None and price >= pos.target_price:
                return self._build_exit("take_profit")
        elif pos.side == "short":
            if pos.stop_price is not None and price >= pos.stop_price:
                return self._build_exit("stop_loss")
            if pos.target_price is not None and price <= pos.target_price:
                return self._build_exit("take_profit")
        return None

    def _build_exit(self, reason: str) -> SignalIntent:
        side = self.state.position.side
        action: SignalActionT = "EXIT_LONG" if side == "long" else "EXIT_SHORT"
        self.state.to_pending_exit()
        self._pending_exit_action = action
        self.debug.exits += 1
        return self._build_intent(action, reason, self._last_accepted_price)

    def confirm_exit_filled(self, realized_pnl_points: Optional[float] = None) -> None:
        if self.state.is_pending():
            self.state.confirm_exit()
            self.risk.on_exit(realized_pnl_points, now_ms())
            if self.risk.state.halted:
                self.halt(self.risk.state.halt_reason or "risk_halted")
        self._pending_exit_action = None

    # ---- output plumbing ---- #

    def _build_intent(self, action: SignalActionT, reason: str,
                      trigger_price: Optional[float]) -> SignalIntent:
        intent = SignalIntent(action=action, reason=reason, trigger_price=trigger_price)
        if self._emit_cb:
            try:
                self._emit_cb(intent)
            except Exception:
                log.exception("emit callback raised")
        return intent


def run_ticks(engine: StrategyEngine, ticks: Iterable[PriceTick]) -> list[SignalIntent]:
    """Convenience helper (mostly for tests / replay)."""
    out: list[SignalIntent] = []
    for t in ticks:
        out.extend(engine.on_tick(t))
    return out
