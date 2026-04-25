"""
Supervisor: owns runtime mode and state. Consumes intents and acks, runs
watchdogs, halts on unsafe conditions. Runs as a set of threads around the
event bus.

Threads (daemon):
  - capture thread  (owned by PriceStream) -> price_queue
  - strategy thread (this module)          <- price_queue, -> intent_queue
  - execution thread (this module)         <- intent_queue, -> ack_queue
  - supervisor main loop                   <- ack_queue, command_queue
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.capture.models import PriceTick
from app.capture.position_watcher import EntryPriceWatcher, PositionWatcher
from app.capture.price_stream import PriceStream
from app.execution.executor import Executor
from app.execution.models import ExecutionAck, ExecutionIntent
from app.models.common import ScreenMap
from app.models.config import BotConfig
from app.strategy.engine import StrategyEngine
from app.strategy.models import SignalIntent
from app.utils import paths
from app.utils.time_utils import now_ms, session_id

from .event_bus import EventBus
from .runtime_models import (ComponentHealth, CommandName, RuntimeCommand, RuntimeMode,
                             RuntimeState)
from .trade_journal import TradeJournal
from .watchdogs import (WatchdogConfig, anchor_watchdog, execution_watchdog,
                        first_halt_reason, price_watchdog, queue_watchdog,
                        value_silence_watchdog)

log = logging.getLogger(__name__)


@dataclass
class SupervisorDeps:
    bot_cfg: BotConfig
    screen_map: ScreenMap
    executor: Executor
    engine: StrategyEngine
    # Optional: when wired, the supervisor reports every position state
    # change so completed trades are recorded to the journal's in-memory
    # session list AND its SQLite store. Tests can pass an in-memory
    # journal; production passes a file-backed one from bootstrap.
    journal: Optional[TradeJournal] = None


class Supervisor:
    STATUS_PRINT_SECONDS = 5.0

    # How often the watchdog loop re-checks pause/halt conditions. Smaller
    # than the anchor probe so state transitions feel responsive.
    WATCHDOG_TICK_SECONDS = 0.5
    # How often to probe the live anchor similarity. User-configurable.
    ANCHOR_PROBE_SECONDS = 3.0

    def __init__(self, deps: SupervisorDeps, state: RuntimeState,
                 bus: Optional[EventBus] = None,
                 watchdog_cfg: Optional[WatchdogConfig] = None,
                 anchor_probe_seconds: float = ANCHOR_PROBE_SECONDS):
        self.deps = deps
        self.state = state
        self.bus = bus or EventBus.create()
        self.watchdog_cfg = watchdog_cfg or WatchdogConfig()
        self.anchor_probe_seconds = anchor_probe_seconds

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_status_ts = 0.0
        self._component_health = ComponentHealth()
        self._price_stream: Optional[PriceStream] = None
        self._position_watcher: Optional[PositionWatcher] = None
        self._entry_price_watcher: Optional[EntryPriceWatcher] = None

        # persistence
        self._state_path = paths.state_dir() / f"runtime_state_{session_id()}.json"

        # anchor probe bookkeeping
        self._last_anchor_probe_ts: float = 0.0

    # ------------------- lifecycle ------------------- #

    def start(self) -> None:
        log.info("Supervisor starting (mode=%s, armed=%s)", self.state.mode, self.state.armed)

        self._price_stream = PriceStream(
            region=self.deps.screen_map.price_region,
            monitor_index=self.deps.screen_map.monitor_index,
            bot_cfg=self.deps.bot_cfg,
            on_tick=self._enqueue_price_tick,
        )
        self._price_stream.start()

        # Two independent broker-panel watchers, each keyed to its own
        # calibrated cell. Either can be enabled on its own, but they're
        # most useful together: the size watcher gives side + contract
        # count from the signed integer; the entry-price watcher gives the
        # verified fill for PnL. Both cells update rarely (only on position
        # transitions), so the watchers effectively go idle while you hold
        # a position — no wasted OCR.
        sm = self.deps.screen_map
        if sm.position_size_region is not None:
            self._position_watcher = PositionWatcher(
                region=sm.position_size_region,
                monitor_index=sm.monitor_index,
                bot_cfg=self.deps.bot_cfg,
                on_size=self._on_position_size_changed,
            )
            self._position_watcher.start()
            log.info("PositionWatcher enabled (%s)",
                     sm.position_size_region.model_dump())
        if sm.entry_price_region is not None:
            self._entry_price_watcher = EntryPriceWatcher(
                region=sm.entry_price_region,
                monitor_index=sm.monitor_index,
                bot_cfg=self.deps.bot_cfg,
                on_price=self._on_entry_price_changed,
            )
            self._entry_price_watcher.start()
            log.info("EntryPriceWatcher enabled (%s)",
                     sm.entry_price_region.model_dump())

        self._spawn("strategy", self._strategy_loop)
        self._spawn("executor", self._executor_loop)
        self._spawn("watchdog", self._watchdog_loop)
        # Drain the command queue from its own thread too. main_loop() runs
        # this on the CLI path, but the HUD app never calls main_loop(), so
        # without this arm/disarm/halt/cancel_all would sit in the queue and
        # never be processed.
        self._spawn("commands", self._command_drain_loop)

    def stop(self, timeout: float = 3.0) -> None:
        log.info("Supervisor stopping")
        self._stop.set()
        if self._price_stream:
            self._price_stream.stop(timeout=timeout)
        if self._position_watcher:
            self._position_watcher.stop(timeout=timeout)
        if self._entry_price_watcher:
            self._entry_price_watcher.stop(timeout=timeout)
        for t in self._threads:
            t.join(timeout=timeout)
        try:
            self.deps.executor.close()
        except Exception:
            pass

    def main_loop(self) -> None:
        """Run the supervisor foreground loop (commands + status line)."""
        try:
            while not self._stop.is_set():
                self._drain_commands()
                self._drain_acks()
                self._maybe_print_status()
                self._persist_state()
                time.sleep(0.1)
        except KeyboardInterrupt:
            log.warning("KeyboardInterrupt in supervisor main_loop")
        finally:
            self.stop()

    # ------------------- command handling ------------------- #

    def submit_command(self, cmd: CommandName, **metadata) -> None:
        try:
            self.bus.command_queue.put_nowait(RuntimeCommand(command=cmd, metadata=metadata))
        except queue.Full:
            log.warning("command queue full, dropping %s", cmd)

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self.bus.command_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_command(cmd)

    def _handle_command(self, cmd: RuntimeCommand) -> None:
        log.info("command: %s %s", cmd.command, cmd.metadata)
        if cmd.command == "pause":
            self._set_mode("PRICE_DEBUG")
            self.state.armed = False
        elif cmd.command == "resume":
            if self.state.halted:
                log.warning("resume ignored while halted; use 'resume_from_halt' pattern: first disarm+halt flag cleared")
            else:
                self._set_mode("PAPER" if self.state.mode == "HALTED" else self.state.mode)
        elif cmd.command == "halt":
            self._halt(cmd.metadata.get("reason", "operator_halt"))
        elif cmd.command == "arm":
            self._try_arm()
        elif cmd.command == "disarm":
            self._set_armed(False)
        elif cmd.command == "cancel_all":
            self._manual_cancel_all()
        elif cmd.command == "status":
            self._print_status(force=True)
        elif cmd.command == "quit":
            self._stop.set()

    def _try_arm(self) -> None:
        if self.state.halted:
            log.warning("cannot arm while halted (%s)", self.state.halt_reason)
            return
        if self.state.paused:
            log.warning("cannot arm while paused (%s)", self.state.pause_reason)
            return
        self.state.armed = True
        self.state.mode = "ARMED"
        self.deps.executor.set_dry_run(False)
        log.warning("!!! ARMED !!! live clicks ENABLED")

    def _set_armed(self, armed: bool) -> None:
        self.state.armed = armed
        self.deps.executor.set_dry_run(not armed)
        if not armed and self.state.mode == "ARMED":
            self.state.mode = "PAPER"
        log.info("armed=%s dry_run=%s", armed, self.deps.executor.config.dry_run)

    def _set_mode(self, mode: RuntimeMode) -> None:
        old = self.state.mode
        self.state.mode = mode
        if mode != "ARMED":
            self.state.armed = False
            self.deps.executor.set_dry_run(True)
        log.info("mode %s -> %s", old, mode)

    def _halt(self, reason: str) -> None:
        if self.state.halted:
            return
        self.state.halted = True
        self.state.halt_reason = reason
        self.state.mode = "HALTED"
        self._set_armed(False)
        try:
            self.deps.engine.halt(reason)
        except Exception:
            log.exception("engine.halt raised")
        log.error("HALTED: %s", reason)

    def _manual_cancel_all(self) -> None:
        intent = ExecutionIntent(action="CANCEL_ALL", reason="operator_cancel_all")
        ack = self.deps.executor.execute(intent)
        log.info("manual CANCEL_ALL ack: %s (%s)", ack.status, ack.message)

    # ------------------- producers / consumers ------------------- #

    def _enqueue_price_tick(self, tick: PriceTick) -> None:
        try:
            self.bus.price_queue.put_nowait(tick)
        except queue.Full:
            # drop oldest to keep freshness
            try:
                self.bus.price_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.bus.price_queue.put_nowait(tick)
            except queue.Full:
                log.warning("price queue full (dropped tick)")

    def _strategy_loop(self) -> None:
        # Short queue timeout keeps UI-visible latency low. A fresh tick can
        # sit in the queue at most this long before we process it, so this
        # becomes the lower bound on end-to-end price-to-HUD latency.
        while not self._stop.is_set():
            try:
                tick = self.bus.price_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            self.state.last_price_tick_ts_ms = tick.ts_ms
            if tick.accepted and tick.price is not None:
                # Track value-change time separately from tick-time. A
                # closed market can stream confirming reads of the same
                # price for hours; we want to know when the market is
                # actually MOVING, not when OCR last spoke.
                if self.state.last_price != tick.price:
                    self.state.last_price_change_ts_ms = tick.ts_ms
                self.state.last_price = tick.price

            # update engine health gate
            ph = self._price_stream.get_health().health_state if self._price_stream else "ok"
            self._component_health.price_stream_health = ph
            self.state.price_stream_health = ph
            self.deps.engine.set_price_stream_ok(ph == "ok")

            if self.state.mode == "PRICE_DEBUG" or self.state.halted:
                continue

            intents = self.deps.engine.on_tick(tick)
            for intent in intents:
                self._publish_intent(intent)

    def _publish_intent(self, intent: SignalIntent) -> None:
        self.state.last_intent_action = intent.action
        try:
            self.bus.intent_queue.put(intent, timeout=0.2)
        except queue.Full:
            log.warning("intent queue full; dropping %s (id=%s)", intent.action, intent.intent_id)

    def _executor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                intent = self.bus.intent_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            exec_intent = self._to_execution_intent(intent)
            if exec_intent is None:
                # exits and cancel_alls both become execution intents above,
                # so this path means unknown action.
                continue

            ack = self.deps.executor.execute(exec_intent)
            self.state.last_execution_ack_ts_ms = ack.ts_ms
            self.state.last_ack_status = ack.status
            self._component_health.consecutive_unknown_acks = (
                self.deps.executor.consecutive_unknown_acks
            )

            # feed back the engine state machine
            self._reconcile_ack(intent, ack)

            try:
                self.bus.ack_queue.put_nowait(ack)
            except queue.Full:
                pass

    def _reconcile_ack(self, intent: SignalIntent, ack: ExecutionAck) -> None:
        # Either watcher is a source of broker-side truth, so 'watcher_wired'
        # means "we can defer an unknown ack to OCR truth instead of halting".
        watcher_wired = (
            self._position_watcher is not None
            or self._entry_price_watcher is not None
        )
        if intent.action in ("BUY", "SELL"):
            if ack.status == "ok":
                # Prefer the broker's verified fill price from AckReader OCR.
                # Fall back to the pre-click trigger price only when OCR is
                # unavailable (status="ok" but fill_price missing).
                fill = ack.fill_price if ack.fill_price is not None else intent.trigger_price
                self.deps.engine.confirm_entry_filled(fill)
                self.state.current_position_side = (
                    "long" if intent.action == "BUY" else "short"
                )
                # stash for the UI
                self.state.last_fill_price = ack.fill_price
                self.state.last_fill_price_source = ack.fill_price_source
            elif ack.status == "failed" or ack.status == "blocked":
                self.deps.engine.reject_entry(f"{ack.status}:{ack.message}")
            elif ack.status == "unknown":
                if watcher_wired:
                    # Trust the PositionWatcher: when size goes from 0->N
                    # it'll call _on_position_size_changed which confirms
                    # the entry. Until then, stay in PENDING_ENTRY.
                    log.info(
                        "unknown_ack_on_entry:%s — deferring to PositionWatcher",
                        ack.message,
                    )
                    self._absorb_deferred_unknown_ack()
                else:
                    self._halt(f"unknown_ack_on_entry:{ack.message}")
        elif intent.action in ("EXIT_LONG", "EXIT_SHORT"):
            if ack.status == "ok":
                self.deps.engine.confirm_exit_filled(realized_pnl_points=None)
                self.state.current_position_side = "flat"
                # clear verified fill on exit — no open position to track
                self.state.last_fill_price = None
                self.state.last_fill_price_source = None
            elif ack.status == "unknown":
                if watcher_wired:
                    log.info(
                        "unknown_ack_on_exit:%s — deferring to PositionWatcher",
                        ack.message,
                    )
                    self._absorb_deferred_unknown_ack()
                else:
                    self._halt(f"unknown_ack_on_exit:{ack.message}")
        elif intent.action == "CANCEL_ALL":
            if ack.status == "ok":
                # Tradovate cleared the position — clear our tracking too
                self.state.last_fill_price = None
                self.state.last_fill_price_source = None
            elif ack.status == "unknown" and watcher_wired:
                # CANCEL_ALL: PositionWatcher sees size → 0 when it actually
                # cleared. Don't count this as a halt-worthy unknown ack.
                log.info(
                    "unknown_ack_on_cancel:%s — deferring to PositionWatcher",
                    ack.message,
                )
                self._absorb_deferred_unknown_ack()

    def _absorb_deferred_unknown_ack(self) -> None:
        """The AckReader returned 'unknown' but the PositionWatcher is wired,
        so the real-source-of-truth (broker's own position panel) will tell
        us what happened. Roll back the executor's streak counter so the
        watchdog doesn't halt on a case we're already handling correctly."""
        ex = self.deps.executor
        if ex.consecutive_unknown_acks > 0:
            ex.consecutive_unknown_acks -= 1
        self._component_health.consecutive_unknown_acks = ex.consecutive_unknown_acks

    # ---- PositionWatcher integration ---- #

    def _on_position_size_changed(self, new_size: int) -> None:
        """Called from PositionWatcher whenever the position-size cell
        changes. `new_size` is SIGNED: >0 = long, <0 = short, 0 = flat.

        Drives engine state on three kinds of edges:
          - 0 -> in-position (open)
          - in-position -> 0 (close)
          - long <-> short (direct reversal, no flat in between)

        Always mirrors the broker's reported side + count onto the UI
        state so the HUD matches reality even when the bot didn't initiate
        the trade."""
        try:
            engine = self.deps.engine
            prev_abs = self.state.position_size or 0
            prev_side = self.state.current_position_side or "flat"
            new_abs = abs(new_size)
            new_side: str = ("long" if new_size > 0
                             else "short" if new_size < 0
                             else "flat")

            # Broker is ground truth for both side and contract count.
            self.state.position_size = new_abs
            self.state.current_position_side = new_side  # type: ignore[assignment]

            # Cross-check vs the user's last raw HUD click. Sign wins (it's
            # what the broker actually did), but log a warning so the
            # operator knows their click didn't land the way they expected.
            if new_side in ("long", "short") and self.state.last_manual_click_action:
                expected = {"BUY": "long", "SELL": "short"}.get(
                    self.state.last_manual_click_action
                )
                if expected and expected != new_side:
                    log.warning(
                        "position side mismatch: last HUD click was %s (expected %s) "
                        "but broker shows %s — trusting broker",
                        self.state.last_manual_click_action, expected, new_side,
                    )

            was_in_position = prev_side in ("long", "short")
            now_in_position = new_side in ("long", "short")
            is_flip = was_in_position and now_in_position and prev_side != new_side

            if is_flip:
                # Long<->short reversal with no flat between. Treat as
                # close+reopen: sync the engine out of its old side, clear
                # the stale fill price so PnL doesn't briefly compute
                # against the OLD entry with the NEW side, and nudge the
                # entry-price watcher so it re-emits when the cell refreshes
                # (handles the race where entry_price already updated BEFORE
                # size did).
                if engine.state.is_in_position():
                    engine.state.to_pending_exit()
                    engine.confirm_exit_filled(realized_pnl_points=None)
                self.state.last_fill_price = None
                self.state.last_fill_price_source = None
                self.state.last_manual_click_action = None
                if self._entry_price_watcher is not None:
                    self._entry_price_watcher.invalidate()
                log.info(
                    "position flipped %s %d -> %s %d — engine synced, fill cleared",
                    prev_side, prev_abs, new_side, new_abs,
                )
            elif new_abs > 0 and prev_abs == 0:
                if engine.state.state == "PENDING_ENTRY":
                    fill = self.state.last_fill_price or engine._last_accepted_price
                    engine.confirm_entry_filled(fill)
                    log.info(
                        "position opened (%s %d) — entry confirmed via watcher",
                        new_side, new_abs,
                    )
                else:
                    log.info(
                        "position opened externally (%s %d) — engine stays FLAT",
                        new_side, new_abs,
                    )
            elif new_abs == 0 and prev_abs > 0:
                if engine.state.state == "PENDING_EXIT":
                    engine.confirm_exit_filled(realized_pnl_points=None)
                    log.info("position closed — exit confirmed via watcher")
                elif engine.state.is_in_position():
                    engine.state.to_pending_exit()
                    engine.confirm_exit_filled(realized_pnl_points=None)
                    log.info("position closed externally — engine synced to FLAT")
                self.state.last_manual_click_action = None
                # No position -> no meaningful fill. Clear immediately so
                # the HUD's PnL row hides on the same refresh tick instead
                # of lagging until the entry-price watcher sees a blank cell.
                self.state.last_fill_price = None
                self.state.last_fill_price_source = None

            self._notify_journal()
        except Exception:
            log.exception("position size handler failed")

    def _on_entry_price_changed(self, price: Optional[float]) -> None:
        """Called from EntryPriceWatcher whenever the entry-price cell
        changes. `price` is None when the cell is blank/unparseable
        (typically because we're flat) — in that case clear the stale
        fill so the HUD's PnL line flips back to '—'."""
        try:
            self.state.last_fill_price = price
            self.state.last_fill_price_source = (
                "position_ocr" if price is not None else None
            )
            self._notify_journal()
        except Exception:
            log.exception("entry price handler failed")

    def _notify_journal(self) -> None:
        """Push the current position state to the TradeJournal, if wired.
        Called at the end of both _on_position_size_changed and
        _on_entry_price_changed so the journal sees a coherent snapshot
        regardless of which watcher fired."""
        journal = self.deps.journal
        if journal is None:
            return
        try:
            journal.position_observed(
                side=self.state.current_position_side,
                size=self.state.position_size or 0,
                fill_price=self.state.last_fill_price,
                last_price=self.state.last_price,
                ts_ms=now_ms(),
            )
        except Exception:
            log.exception("trade journal hook failed")

    def _to_execution_intent(self, intent: SignalIntent) -> Optional[ExecutionIntent]:
        a = intent.action
        if a == "BUY":
            return ExecutionIntent(action="BUY", reason=intent.reason)
        if a == "SELL":
            return ExecutionIntent(action="SELL", reason=intent.reason)
        if a in ("EXIT_LONG", "EXIT_SHORT"):
            # closing a long = SELL; closing a short = BUY
            return ExecutionIntent(
                action="SELL" if a == "EXIT_LONG" else "BUY",
                reason=intent.reason,
            )
        if a == "CANCEL_ALL":
            return ExecutionIntent(action="CANCEL_ALL", reason=intent.reason)
        return None

    def _drain_acks(self) -> None:
        # Currently informational — the reconcile path already applied state.
        while True:
            try:
                _ = self.bus.ack_queue.get_nowait()
            except queue.Empty:
                return

    # ------------------- command drain ------------------- #

    def _command_drain_loop(self) -> None:
        """Pulls RuntimeCommands off the bus every 100ms and dispatches them.
        main_loop() does the same thing on the CLI path; the HUD app skips
        main_loop(), so without this dedicated thread arm / disarm / halt /
        cancel_all submissions from the HUD would queue up and never run."""
        while not self._stop.is_set():
            try:
                self._drain_commands()
            except Exception:
                log.exception("command drain failed")
            time.sleep(0.1)

    # ------------------- watchdog ------------------- #

    def _watchdog_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.WATCHDOG_TICK_SECONDS)
            if self.state.halted:
                continue

            # Refresh the anchor similarity check on a fixed cadence so the
            # pause state reflects whether Tradovate is actually visible.
            self._maybe_probe_anchor()

            # Halt-class reasons: unrecoverable or demand operator attention.
            # These keep the existing HALT behavior.
            halt_reason = first_halt_reason([
                execution_watchdog(self._component_health.consecutive_unknown_acks,
                                   self.watchdog_cfg),
                queue_watchdog({
                    "price": self.bus.price_queue.qsize(),
                    "intent": self.bus.intent_queue.qsize(),
                    "ack": self.bus.ack_queue.qsize(),
                }, self.watchdog_cfg),
            ])
            if halt_reason:
                self._halt(halt_reason)
                continue

            # Pause-class reasons: transient, auto-recoverable. Price stream
            # broken or stale, anchor guard failing, or the market itself
            # being inactive (closed exchange, weekend, holiday). Trading
            # suspends while paused; resumes as soon as the conditions clear.
            ms_since = now_ms() - (self.state.last_price_tick_ts_ms or now_ms())
            ms_since_change = (
                now_ms() - self.state.last_price_change_ts_ms
                if self.state.last_price_change_ts_ms else 0
            )
            pause_reason = first_halt_reason([
                price_watchdog(self._component_health.price_stream_health, ms_since,
                               self.watchdog_cfg),
                anchor_watchdog(self.state.anchor_guard_ok),
                value_silence_watchdog(ms_since_change, self.watchdog_cfg),
            ])

            if pause_reason:
                self._pause(pause_reason)
            else:
                self._resume_if_paused()

    def _maybe_probe_anchor(self) -> None:
        """Run the live anchor-similarity check every `anchor_probe_seconds`."""
        now_s = time.time()
        if now_s - self._last_anchor_probe_ts < self.anchor_probe_seconds:
            return
        self._last_anchor_probe_ts = now_s
        try:
            guard = self.deps.executor.guard
            result = guard.check()
            self.state.anchor_guard_ok = result.ok
            if not result.ok:
                log.info("anchor probe: %s", result.as_message())
        except Exception as e:
            log.warning("anchor probe failed: %s", e)
            self.state.anchor_guard_ok = False

    # ------------------- pause / resume ------------------- #

    def _pause(self, reason: str) -> None:
        """Transient suspension. Keeps the supervisor running; trading is off."""
        if self.state.halted:
            return
        if not self.state.paused or self.state.pause_reason != reason:
            log.warning("PAUSED: %s", reason)
        self.state.paused = True
        self.state.pause_reason = reason
        # Suspending ARMED mode while paused: engine's price_stream_ok flag is
        # already driven off health in the strategy loop, so entries won't fire.
        # submit_manual_intent also refuses when paused (see engine).
        try:
            self.deps.engine.set_price_stream_ok(False)
        except Exception:
            pass

    def _resume_if_paused(self) -> None:
        if not self.state.paused:
            return
        log.info("RESUMED from pause (was: %s)", self.state.pause_reason)
        self.state.paused = False
        self.state.pause_reason = None
        # Engine will pick up fresh health in the strategy loop; still set
        # ok=True explicitly so a pending bar close can trigger on the next
        # tick instead of waiting for the next health update.
        try:
            health = self._price_stream.get_health() if self._price_stream else None
            if health is not None:
                self.deps.engine.set_price_stream_ok(health.health_state == "ok")
            else:
                self.deps.engine.set_price_stream_ok(True)
        except Exception:
            pass

    # ------------------- helpers ------------------- #

    def _spawn(self, name: str, target) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _maybe_print_status(self) -> None:
        now = time.time()
        if now - self._last_status_ts < self.STATUS_PRINT_SECONDS:
            return
        self._print_status()
        self._last_status_ts = now

    def _print_status(self, force: bool = False) -> None:  # noqa: ARG002
        price_str = f"{self.state.last_price:.2f}" if self.state.last_price is not None else "----"
        line = (
            f"MODE={self.state.mode} | ARMED={self.state.armed} | "
            f"PRICE={price_str} | HEALTH={self.state.price_stream_health} | "
            f"POS={self.state.current_position_side} | "
            f"LAST_INTENT={self.state.last_intent_action or '-'} | "
            f"LAST_ACK={self.state.last_ack_status or '-'} | "
            f"HALT={self.state.halt_reason or '-'}"
        )
        log.info(line)

    def _persist_state(self) -> None:
        try:
            self._state_path.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            log.debug("state persist failed", exc_info=True)
