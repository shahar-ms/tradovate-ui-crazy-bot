"""
Central signal hub for the operator UI.

Any worker thread or controller that wants the UI to update emits on this
object. Pages and widgets connect to the signals they care about. This
decouples UI pages from the Supervisor.

All signals carry plain dicts or primitives so they can be safely marshalled
across threads via queued connections.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppSignals(QObject):
    # runtime state changes
    mode_changed = Signal(str)                 # "PRICE_DEBUG" | "PAPER" | "ARMED" | "HALTED" | ...
    armed_changed = Signal(bool)
    halt_triggered = Signal(str)               # halt reason
    halt_cleared = Signal()

    # market / OCR
    price_updated = Signal(dict)               # PriceTick.model_dump()
    health_updated = Signal(dict)              # StreamHealth.model_dump()

    # strategy
    position_changed = Signal(str)             # "flat" | "long" | "short"
    signal_emitted = Signal(dict)              # SignalIntent.model_dump()

    # execution
    execution_ack = Signal(dict)               # ExecutionAck.model_dump()

    # guard / calibration
    anchor_guard_changed = Signal(bool, float) # (ok, similarity)
    calibration_reloaded = Signal()

    # logs / events
    event_logged = Signal(dict)                # {"ts_ms", "level", "source", "message"}

    # connection to runtime (controller lifecycle)
    controller_state_changed = Signal(str)     # "stopped" | "running" | "error"

    # floating HUD -> main window
    hud_show_main_requested = Signal()


# Convenience helper for emitting an event log row from anywhere.
def emit_event(signals: AppSignals, level: str, source: str, message: str) -> None:
    from app.utils.time_utils import now_ms
    signals.event_logged.emit({
        "ts_ms": now_ms(),
        "level": level,
        "source": source,
        "message": message,
    })
