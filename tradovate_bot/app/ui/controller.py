"""
UiController: the bridge between the Supervisor (background threads) and
the Qt UI (main thread).

Responsibilities:
  - own the Supervisor lifecycle (start, stop, switch mode, arm/halt)
  - poll the Supervisor state + engine + executor on a Qt timer
  - emit AppSignals so pages refresh reactively
  - present safety-gated commands (arm requires paper + health ok)

The controller never blocks the UI thread. All it does on the UI thread
is read light-weight state snapshots from the Supervisor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Slot

from app.capture.models import PriceTick
from app.execution.models import ExecutionAck
from app.orchestrator.bootstrap import BootstrapError, bootstrap
from app.orchestrator.runtime_models import RuntimeState
from app.orchestrator.supervisor import Supervisor, SupervisorDeps
from app.strategy.models import SignalIntent
from app.strategy.pnl import compute_pnl
from app.utils.time_utils import now_ms

from .app_signals import AppSignals, emit_event
from .ui_state import UiState

log = logging.getLogger(__name__)


@dataclass
class ControllerConfig:
    poll_interval_ms: int = 250


class UiController(QObject):
    def __init__(self, signals: AppSignals, state: UiState,
                 cfg: Optional[ControllerConfig] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.signals = signals
        self.state = state
        self.cfg = cfg or ControllerConfig()

        self._supervisor: Optional[Supervisor] = None
        self._started_at_ms: Optional[int] = None
        self._last_emitted_frame_id: int = -1
        # stash last bootstrap failure so the UI can show a detailed dialog
        self.last_start_error: Optional[str] = None
        self.last_start_report_lines: list[str] = []

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.cfg.poll_interval_ms)
        self._poll_timer.timeout.connect(self._poll_once)

    # ---------------- lifecycle ---------------- #

    def is_running(self) -> bool:
        return self._supervisor is not None

    def start(self, mode: str = "PRICE_DEBUG", armed: bool = False,
              skip_calibration_check: bool = False) -> Optional[str]:
        """
        Start (or restart) the supervisor in the given mode. Returns an
        error string on failure, None on success.
        """
        if self._supervisor is not None:
            return "already_running"

        try:
            br = bootstrap(
                initial_mode=mode,  # type: ignore[arg-type]
                armed=armed,
                skip_calibration_check=skip_calibration_check,
            )
        except BootstrapError as e:
            log.error("bootstrap failed: %s", e)
            emit_event(self.signals, "error", "controller", f"bootstrap: {e}")
            self.signals.controller_state_changed.emit("error")
            self.last_start_error = str(e)
            self.last_start_report_lines = list(getattr(e, "report_lines", []) or [])
            return str(e)

        self.last_start_error = None
        self.last_start_report_lines = []

        # hook the engine emit callback to the signal bus
        br.engine._emit_cb = self._on_engine_intent  # type: ignore[attr-defined]

        deps = SupervisorDeps(
            bot_cfg=br.bot_cfg,
            screen_map=br.screen_map,
            executor=br.executor,
            engine=br.engine,
        )
        self._supervisor = Supervisor(deps=deps, state=br.starting_state)
        self._supervisor.start()
        self._started_at_ms = now_ms()

        # sync UiState with bootstrap
        self.state.session_id = br.starting_state.session_id
        self.state.mode = br.starting_state.mode
        self.state.armed = br.starting_state.armed
        self.state.halted = False
        self.state.halt_reason = None
        self.state.calibration_loaded = True
        self.state.monitor_index = br.screen_map.monitor_index
        self.state.screen_size = (br.screen_map.screen_width, br.screen_map.screen_height)

        self.signals.mode_changed.emit(self.state.mode)
        self.signals.armed_changed.emit(self.state.armed)
        self.signals.calibration_reloaded.emit()
        self.signals.controller_state_changed.emit("running")
        emit_event(self.signals, "info", "controller", f"started mode={mode} armed={armed}")

        self._poll_timer.start()
        return None

    def stop(self) -> None:
        self._poll_timer.stop()
        if self._supervisor is not None:
            try:
                self._supervisor.stop(timeout=3.0)
            except Exception:
                log.exception("supervisor.stop raised")
            self._supervisor = None
        self.state.mode = "DISCONNECTED"
        self.state.armed = False
        self.signals.mode_changed.emit(self.state.mode)
        self.signals.controller_state_changed.emit("stopped")
        emit_event(self.signals, "info", "controller", "stopped")

    # ---------------- operator commands ---------------- #

    def arm(self) -> Optional[str]:
        if self._supervisor is None:
            return "not_running"
        checks = self.pre_arm_checks()
        blocking = [c for c in checks if not c.ok]
        if blocking:
            return "; ".join(c.reason for c in blocking)
        self._supervisor.submit_command("arm")
        emit_event(self.signals, "warn", "controller", "ARM requested")
        return None

    def disarm(self) -> None:
        if self._supervisor is None:
            return
        self._supervisor.submit_command("disarm")
        emit_event(self.signals, "info", "controller", "disarm requested")

    # ---- high-level enable / disable of auto trading ---- #

    def set_auto_enabled(self, enabled: bool) -> None:
        """Toggle the strategy's auto-trading. When False the engine stops
        emitting entry/exit intents; manual buttons + OCR keep running."""
        if self._supervisor is None:
            return
        self._supervisor.deps.engine.auto_enabled = enabled
        self.state.auto_enabled = enabled
        emit_event(self.signals, "info", "controller",
                   f"auto trading {'ENABLED' if enabled else 'DISABLED'}")

    def disable_bot(self) -> Optional[str]:
        """High-level 'Disable Bot': stop auto trading but keep the armed
        execution path (so manual BUY/SELL still click for real)."""
        if self._supervisor is None:
            return "not_running"
        self.set_auto_enabled(False)
        return None

    # ---- single-toggle ON/OFF (the simplified HUD uses these) ---- #

    def turn_on(self) -> Optional[str]:
        """Bot ON = full live mode: armed (manual + auto clicks go live)
        AND strategy auto-trades entries/exits. Returns an error string if
        arming fails (e.g. pre-arm checks blocked)."""
        if self._supervisor is None:
            return "not_running"
        err = self.arm()
        if err is not None:
            return err
        self.set_auto_enabled(True)
        return None

    def turn_off(self) -> Optional[str]:
        """Bot OFF = price OCR keeps running, but no trading: strategy
        silent AND execution disarmed (clicks simulated, can't reach
        Tradovate). Safe default."""
        if self._supervisor is None:
            return None
        # Disable strategy first, then disarm — avoids a one-tick window
        # where the strategy could still emit.
        self.set_auto_enabled(False)
        self.disarm()
        return None

    def halt(self, reason: str = "operator_halt") -> None:
        if self._supervisor is None:
            return
        self._supervisor.submit_command("halt", reason=reason)
        emit_event(self.signals, "error", "controller", f"HALT requested: {reason}")

    def cancel_all(self) -> None:
        if self._supervisor is None:
            return
        self._supervisor.submit_command("cancel_all")
        emit_event(self.signals, "warn", "controller", "cancel_all requested")

    def submit_manual(self, action: str) -> tuple[bool, str]:
        """
        Route a HUD-originated click through the strategy engine. Returns
        (accepted, message). On rejection, emits `manual_rejected(message)`
        so the HUD can show a short toast.
        """
        if self._supervisor is None:
            msg = "bot not running"
            self.signals.manual_rejected.emit(msg)
            return False, msg

        # Surface pause as a distinct, clearer rejection message (the engine
        # itself would just say "price stream not ok" because we suspend it
        # during pause).
        sup_state = self._supervisor.state
        if sup_state.paused and action in ("BUY", "SELL"):
            msg = f"paused — {sup_state.pause_reason or 'screen not visible'}"
            emit_event(self.signals, "warn", "controller",
                       f"manual {action} rejected while paused: {msg}")
            self.signals.manual_rejected.emit(msg)
            return False, msg

        engine = self._supervisor.deps.engine
        try:
            ok, msg, intents = engine.submit_manual_intent(action)  # type: ignore[arg-type]
        except Exception as e:
            log.exception("submit_manual_intent raised")
            ok, msg, intents = False, f"engine error: {e}", []
        if ok:
            # Forward the emitted intents onto the bus so the executor picks
            # them up. The engine intentionally doesn't publish; the UI
            # controller owns the bus.
            for intent in intents:
                try:
                    self._supervisor._publish_intent(intent)
                except Exception:
                    log.exception("failed to publish manual intent")
            emit_event(self.signals, "info", "controller",
                       f"manual {action}: {msg} ({len(intents)} intent(s))")
        else:
            emit_event(self.signals, "warn", "controller", f"manual {action} rejected: {msg}")
            self.signals.manual_rejected.emit(msg)
        return ok, msg

    def reload_executor_screen_map(self, new_map) -> None:
        """Called by the HUD after a Setup/calibration save."""
        if self._supervisor is None:
            return
        try:
            self._supervisor.deps.executor.reload_screen_map(new_map)
            emit_event(self.signals, "info", "controller", "executor screen_map reloaded")
        except Exception as e:
            log.exception("executor.reload_screen_map raised")
            emit_event(self.signals, "error", "controller", f"reload failed: {e}")

    def switch_mode(self, mode: str) -> Optional[str]:
        """Switch mode by restarting the supervisor in the new mode."""
        if mode not in ("PRICE_DEBUG", "PAPER", "ARMED"):
            return f"invalid_mode:{mode}"
        was_running = self.is_running()
        if was_running:
            self.stop()
        return self.start(mode=mode, armed=(mode == "ARMED"))

    # ---------------- pre-arm checks ---------------- #

    def pre_arm_checks(self) -> list["PreArmCheck"]:
        checks: list[PreArmCheck] = []
        checks.append(PreArmCheck("calibration_loaded", self.state.calibration_loaded,
                                  "calibration not loaded"))
        checks.append(PreArmCheck("not_halted", not self.state.halted,
                                  f"halted ({self.state.halt_reason})"))
        checks.append(PreArmCheck("price_health_ok",
                                  self.state.price_stream_health == "ok",
                                  f"price_stream_health={self.state.price_stream_health}"))
        checks.append(PreArmCheck("anchor_guard_ok", self.state.anchor_ok,
                                  "anchor guard not ok"))
        checks.append(PreArmCheck("not_paused", not self.state.paused,
                                  f"paused ({self.state.pause_reason or '?'})"))
        return checks

    # ---------------- engine -> signal bridge ---------------- #

    def _on_engine_intent(self, intent: SignalIntent) -> None:
        # runs on the strategy thread — emit queues to the UI thread
        try:
            self.signals.signal_emitted.emit(intent.model_dump())
            self.state.last_intent_action = intent.action
            self.state.last_intent_reason = intent.reason
            self.state.signals_emitted_count += 1
            emit_event(self.signals, "info", "strategy",
                       f"{intent.action} ({intent.reason})")
        except Exception:
            log.exception("failed to bridge engine intent")

    # ---------------- poller ---------------- #

    @Slot()
    def _poll_once(self) -> None:
        sup = self._supervisor
        if sup is None:
            return
        rs: RuntimeState = sup.state

        # mode / armed / halted
        if rs.mode != self.state.mode:
            self.state.mode = rs.mode
            self.signals.mode_changed.emit(rs.mode)
        if rs.armed != self.state.armed:
            self.state.armed = rs.armed
            self.signals.armed_changed.emit(rs.armed)
        if rs.halted and not self.state.halted:
            self.state.halted = True
            self.state.halt_reason = rs.halt_reason
            self.signals.halt_triggered.emit(rs.halt_reason or "unknown")
        elif not rs.halted and self.state.halted:
            self.state.halted = False
            self.state.halt_reason = None
            self.signals.halt_cleared.emit()

        # pause (transient, auto-recovers)
        self.state.paused = rs.paused
        self.state.pause_reason = rs.pause_reason

        # anchor guard result (updated by supervisor's anchor probe)
        self.state.anchor_ok = rs.anchor_guard_ok

        # market
        if rs.last_price is not None and rs.last_price != self.state.last_price:
            self.state.last_price = rs.last_price
            self.state.last_price_ts_ms = rs.last_price_tick_ts_ms
        self.state.price_stream_health = rs.price_stream_health

        # read cumulative counters directly from the stream so we don't
        # double-count the same tick across poll cycles
        if sup._price_stream is not None:
            self.state.accepted_tick_count = sup._price_stream.total_accepted_count
            self.state.rejected_tick_count = sup._price_stream.total_rejected_count
            self.state.last_raw_text = sup._price_stream.last_raw_text
            self.state.last_reject_reason = sup._price_stream.last_reject_reason
            self.state.last_ocr_ms = sup._price_stream.last_ocr_ms
            self.state.last_frame_ms = sup._price_stream.last_frame_ms
            self.state.total_deduped_count = sup._price_stream.total_deduped_count

        latest = sup._price_stream.get_latest_tick() if sup._price_stream else None
        if latest is not None:
            self.state.last_confidence = latest.confidence
            # emit only on new frame ids to avoid UI spam
            if self._last_emitted_frame_id != latest.frame_id:
                self._last_emitted_frame_id = latest.frame_id
                self.signals.price_updated.emit({
                    "ts_ms": latest.ts_ms,
                    "price": latest.price,
                    "confidence": latest.confidence,
                    "accepted": latest.accepted,
                    "reject_reason": latest.reject_reason,
                    "frame_id": latest.frame_id,
                })

        # auto-trading toggle (set by HUD Enable/Disable button)
        self.state.auto_enabled = bool(getattr(sup.deps.engine, "auto_enabled", True))

        # position
        pos = sup.deps.engine.state.position
        side = pos.side
        if side != self.state.position_side:
            self.state.position_side = side
            self.signals.position_changed.emit(side)
        self.state.entry_price = pos.entry_price
        self.state.stop_price = pos.stop_price
        self.state.target_price = pos.target_price

        # verified broker fill + PnL
        self.state.fill_price = rs.last_fill_price
        self.state.fill_price_source = rs.last_fill_price_source
        # Only compute PnL when we have a *verified* broker fill price.
        # When fill_price is None (ack succeeded but OCR couldn't verify), show "—".
        if self.state.fill_price is not None and self.state.last_price is not None \
                and side in ("long", "short"):
            pts, usd = compute_pnl(self.state.fill_price, self.state.last_price, side)
            self.state.pnl_points = pts
            self.state.pnl_usd = usd
        else:
            self.state.pnl_points = None
            self.state.pnl_usd = None

        # execution
        self.state.last_ack_status = rs.last_ack_status
        self.state.last_ack_ts_ms = rs.last_execution_ack_ts_ms
        self.state.consecutive_unknown_acks = sup.deps.executor.consecutive_unknown_acks

        # uptime
        if self._started_at_ms:
            self.state.uptime_seconds = (now_ms() - self._started_at_ms) // 1000

        # health emission
        health = sup._price_stream.get_health() if sup._price_stream else None
        if health is not None:
            self.signals.health_updated.emit(health.model_dump())


@dataclass
class PreArmCheck:
    name: str
    ok: bool
    reason: str

    @property
    def icon(self) -> str:
        return "OK" if self.ok else "FAIL"
