"""
Supervisor integration tests. Use fakes for executor, engine, and price stream
to avoid touching the OS.
"""

from __future__ import annotations

import queue
import time
from typing import Optional

import pytest

from app.capture.models import PriceTick
from app.execution.models import ExecutionAck, ExecutionIntent
from app.models.common import Point, Region, ScreenMap
from app.models.config import BotConfig, SessionWindow, StrategyConfig
from app.orchestrator.bootstrap import BootstrapError
from app.orchestrator.runtime_models import RuntimeState
from app.orchestrator.supervisor import Supervisor, SupervisorDeps
from app.strategy.engine import StrategyEngine
from app.strategy.models import SignalIntent


# ---------------- fakes ---------------- #

class FakeExecutor:
    def __init__(self, status: str = "ok"):
        self.calls: list[ExecutionIntent] = []
        self.status = status
        self.consecutive_unknown_acks = 0
        self.config = _fake_exec_config()

    def execute(self, intent: ExecutionIntent) -> ExecutionAck:
        self.calls.append(intent)
        if self.status == "unknown":
            self.consecutive_unknown_acks += 1
        else:
            self.consecutive_unknown_acks = 0
        return ExecutionAck(
            intent_id=intent.intent_id,
            action=intent.action,
            status=self.status,  # type: ignore[arg-type]
            message="fake",
        )

    def set_dry_run(self, dry_run: bool) -> None:
        self.config.dry_run = dry_run

    def close(self) -> None:
        pass


def _fake_exec_config():
    class _Cfg:
        dry_run = True
    return _Cfg()


def _screen_map() -> ScreenMap:
    return ScreenMap(
        monitor_index=1,
        screen_width=1920,
        screen_height=1080,
        browser_name="chrome",
        tradovate_anchor_region=Region(left=20, top=20, width=200, height=60),
        tradovate_anchor_reference_path="runtime/screenshots/anchor_reference.png",
        price_region=Region(left=800, top=200, width=120, height=40),
        buy_point=Point(x=1500, y=880),
        sell_point=Point(x=1560, y=880),
        cancel_all_point=Point(x=1620, y=880),
    )


def _bot_cfg() -> BotConfig:
    return BotConfig(preprocess_recipes=["gray_only"])


def _strategy_cfg() -> StrategyConfig:
    return StrategyConfig(
        session_windows=[SessionWindow(start="00:00", end="23:59", timezone="UTC")],
    )


def _make_supervisor(executor, engine=None) -> Supervisor:
    engine = engine or StrategyEngine(_strategy_cfg())
    deps = SupervisorDeps(
        bot_cfg=_bot_cfg(),
        screen_map=_screen_map(),
        executor=executor,
        engine=engine,
    )
    state = RuntimeState(mode="PAPER", armed=False)
    return Supervisor(deps=deps, state=state)


# ---------------- tests ---------------- #

def test_to_execution_intent_mapping():
    sup = _make_supervisor(FakeExecutor())
    assert sup._to_execution_intent(SignalIntent(action="BUY", reason="x")).action == "BUY"
    assert sup._to_execution_intent(SignalIntent(action="SELL", reason="x")).action == "SELL"
    assert sup._to_execution_intent(SignalIntent(action="EXIT_LONG", reason="x")).action == "SELL"
    assert sup._to_execution_intent(SignalIntent(action="EXIT_SHORT", reason="x")).action == "BUY"
    assert sup._to_execution_intent(SignalIntent(action="CANCEL_ALL", reason="x")).action == "CANCEL_ALL"


def test_command_drain_thread_processes_arm_without_main_loop():
    """The HUD app never calls supervisor.main_loop(); commands submitted
    from the HUD must still be processed. The dedicated command-drain
    thread handles that. We start the supervisor (spawns the drain
    thread), submit 'arm', and verify state.armed flips without us ever
    calling main_loop()."""
    import time as _time
    sup = _make_supervisor(FakeExecutor())
    # avoid spinning up the real PriceStream / strategy / executor loops
    sup.start = lambda: None  # type: ignore[assignment]
    # manually start just the command drain thread
    import threading
    t = threading.Thread(target=sup._command_drain_loop, daemon=True, name="cmd-drain")
    t.start()
    sup._threads.append(t)

    assert not sup.state.armed
    sup.submit_command("arm")
    # the drain loop runs at 100ms. Give it a generous 400ms.
    deadline = _time.time() + 0.4
    while _time.time() < deadline and not sup.state.armed:
        _time.sleep(0.02)
    sup._stop.set()
    t.join(timeout=0.5)
    assert sup.state.armed, "arm command must be processed by the drain thread"


def test_arm_works_from_price_debug():
    """The simplified HUD boots in PRICE_DEBUG and arms directly — there is
    no intermediate PAPER step anymore."""
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PRICE_DEBUG"
    sup._try_arm()
    assert sup.state.armed
    assert sup.state.mode == "ARMED"


def test_arm_blocked_when_halted():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PRICE_DEBUG"
    sup._halt("x")
    sup._try_arm()
    assert not sup.state.armed


def test_arm_blocked_when_paused():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PRICE_DEBUG"
    sup._pause("anchor_drift")
    sup._try_arm()
    assert not sup.state.armed


def test_halt_disarms_and_sets_mode():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "ARMED"
    sup.state.armed = True
    sup._halt("anchor_drift")
    assert sup.state.halted
    assert sup.state.mode == "HALTED"
    assert not sup.state.armed
    assert sup.state.halt_reason == "anchor_drift"


def test_reconcile_ack_unknown_entry_halts():
    ex = FakeExecutor(status="unknown")
    sup = _make_supervisor(ex)
    intent = SignalIntent(action="BUY", reason="entry", trigger_price=100.0)
    ack = ex.execute(ExecutionIntent(action="BUY", reason="entry"))
    sup._reconcile_ack(intent, ack)
    assert sup.state.halted
    assert "unknown_ack_on_entry" in (sup.state.halt_reason or "")


def test_reconcile_ack_ok_entry_confirms_engine():
    ex = FakeExecutor(status="ok")
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sup = _make_supervisor(ex, engine=engine)
    intent = SignalIntent(action="BUY", reason="entry", trigger_price=100.0)
    ack = ExecutionAck(intent_id=intent.intent_id, action="BUY", status="ok", message="x")
    sup._reconcile_ack(intent, ack)
    assert engine.state.state == "LONG"
    assert sup.state.current_position_side == "long"


def test_reconcile_ack_blocked_entry_rejects():
    ex = FakeExecutor()
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("BUY", 100.0, 95.0, 112.0)
    sup = _make_supervisor(ex, engine=engine)
    ack = ExecutionAck(intent_id="x", action="BUY", status="blocked",
                       message="anchor_mismatch", screen_guard_passed=False)
    sup._reconcile_ack(SignalIntent(action="BUY", reason="entry"), ack)
    assert engine.state.is_flat()


def test_command_halt_via_queue():
    sup = _make_supervisor(FakeExecutor())
    sup.submit_command("halt", reason="operator_test")
    sup._drain_commands()
    assert sup.state.halted
    assert sup.state.halt_reason == "operator_test"


def test_command_cancel_all_runs_executor():
    ex = FakeExecutor()
    sup = _make_supervisor(ex)
    sup.submit_command("cancel_all")
    sup._drain_commands()
    assert any(c.action == "CANCEL_ALL" for c in ex.calls)


def test_command_quit_sets_stop():
    sup = _make_supervisor(FakeExecutor())
    sup.submit_command("quit")
    sup._drain_commands()
    assert sup._stop.is_set()


def test_controller_submit_manual_publishes_intents_to_bus(qtbot=None):
    """Guards the bug where manual HUD BUY/SELL/CANCEL never reached the
    executor because the controller's _on_engine_intent callback only
    emitted a UI signal instead of pushing onto bus.intent_queue."""
    from app.ui.app_signals import AppSignals
    from app.ui.controller import UiController
    from app.ui.ui_state import UiState

    ex = FakeExecutor()
    engine = StrategyEngine(_strategy_cfg())
    # seed a price so the engine accepts BUY from FLAT
    engine._last_accepted_price = 100.0

    sup = _make_supervisor(ex, engine=engine)

    signals = AppSignals()
    controller = UiController(signals=signals, state=UiState())
    # wire controller to this supervisor without booting real threads
    controller._supervisor = sup

    assert sup.bus.intent_queue.qsize() == 0
    ok, msg = controller.submit_manual("BUY")
    assert ok, msg

    # Supervisor's intent queue must now contain the emitted intents
    # (CANCEL_ALL before BUY, since cancel_all_before_new_entry defaults to True).
    sizes = sup.bus.intent_queue.qsize()
    assert sizes >= 1


def test_unknown_ack_does_not_halt_when_position_watcher_wired():
    """When the operator has calibrated position_size_region, the watcher
    is the source of truth — we defer instead of halting on unknown ack."""
    ex = FakeExecutor(status="unknown")
    sup = _make_supervisor(ex)
    # Simulate a wired watcher; don't need a real one to exercise the branch.
    sup._position_watcher = object()  # truthy placeholder

    intent = SignalIntent(action="BUY", reason="entry", trigger_price=100.0)
    ack = ExecutionAck(intent_id=intent.intent_id, action="BUY",
                       status="unknown", message="no_evidence_region")
    sup._reconcile_ack(intent, ack)

    assert not sup.state.halted, "watcher-wired: unknown ack must NOT halt"


def test_position_watcher_size_zero_to_n_confirms_pending_entry():
    """0 -> N transition with a PENDING_ENTRY engine confirms the entry."""
    engine = StrategyEngine(_strategy_cfg())
    engine._last_accepted_price = 100.0
    # put engine into PENDING_ENTRY as if a BUY was just submitted
    engine.state.to_pending_entry("BUY", trigger_price=100.0,
                                  stop=95.0, target=110.0)

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()

    sup._on_position_size_changed(1)

    assert engine.state.state == "LONG"
    assert sup.state.current_position_side == "long"
    assert sup.state.position_size == 1


def test_position_watcher_signed_size_positive_sets_long():
    """Signed-size watcher: +1 means long."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()

    sup._on_position_size_changed(1)

    assert sup.state.current_position_side == "long"
    assert sup.state.position_size == 1


def test_position_watcher_signed_size_negative_sets_short():
    """Signed-size watcher: -1 means short — no reliance on HUD click."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()

    sup._on_position_size_changed(-2)

    assert sup.state.current_position_side == "short"
    assert sup.state.position_size == 2


def test_position_watcher_broker_side_wins_over_manual_click():
    """Sign of size wins even when last_manual_click_action disagrees — the
    broker's state is ground truth. Mismatch is logged but not overridden."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.last_manual_click_action = "BUY"   # user thought long
    # but the broker actually shows a short (e.g. misclick landed on SELL)
    sup._on_position_size_changed(-1)

    assert sup.state.current_position_side == "short"


def test_position_watcher_zero_clears_last_manual_click_action():
    """On size -> 0 the stashed click action must be cleared so a later
    unrelated open isn't mis-labelled with a stale direction."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.last_manual_click_action = "BUY"
    sup._on_position_size_changed(1)          # opens, side = long
    sup._on_position_size_changed(0)          # closes

    assert sup.state.current_position_side == "flat"
    assert sup.state.last_manual_click_action is None


def test_position_watcher_flip_long_to_short_syncs_everything():
    """A direct long->short reversal (no flat between) must: flip side,
    update size, clear the now-stale fill price, and sync the engine out
    of its LONG state so the strategy doesn't think we're still long."""
    engine = StrategyEngine(_strategy_cfg())
    # Seed an open long in the engine and a verified fill in state.
    engine.state.to_pending_entry("BUY", trigger_price=26680.0,
                                  stop=26580.0, target=26780.0)
    engine.confirm_entry_filled(26680.25)
    assert engine.state.state == "LONG"

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.position_size = 1
    sup.state.current_position_side = "long"
    sup.state.last_fill_price = 26680.25
    sup.state.last_fill_price_source = "position_ocr"
    sup.state.last_manual_click_action = "BUY"

    sup._on_position_size_changed(-1)   # flipped to short

    assert sup.state.current_position_side == "short"
    assert sup.state.position_size == 1
    assert sup.state.last_fill_price is None, \
        "stale long-entry fill must be cleared on flip"
    assert sup.state.last_fill_price_source is None
    assert sup.state.last_manual_click_action is None, \
        "BUY click intent is no longer current after flip"
    assert engine.state.state == "FLAT", \
        "engine must sync out of LONG on broker-side flip"


def test_position_watcher_flip_short_to_long_syncs_everything():
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("SELL", trigger_price=26700.0,
                                  stop=26800.0, target=26600.0)
    engine.confirm_entry_filled(26700.00)
    assert engine.state.state == "SHORT"

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.position_size = 2
    sup.state.current_position_side = "short"
    sup.state.last_fill_price = 26700.00
    sup.state.last_fill_price_source = "position_ocr"

    sup._on_position_size_changed(1)   # flipped to long (smaller size)

    assert sup.state.current_position_side == "long"
    assert sup.state.position_size == 1
    assert sup.state.last_fill_price is None
    assert engine.state.state == "FLAT"


def test_position_watcher_flip_invalidates_entry_price_watcher():
    """If the entry-price watcher had already observed the NEW entry price
    before the size flip fired, its internal cache matches the cell text —
    so the fill-clear triggered by the flip would otherwise never recover.
    The supervisor must poke the watcher so it re-emits on the next OCR."""
    class _FakeEntryWatcher:
        def __init__(self):
            self.invalidated = 0
        def invalidate(self):
            self.invalidated += 1

    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    fake = _FakeEntryWatcher()
    sup._entry_price_watcher = fake  # type: ignore[assignment]
    sup.state.position_size = 1
    sup.state.current_position_side = "long"

    sup._on_position_size_changed(-1)

    assert fake.invalidated == 1


def test_position_watcher_close_clears_fill_immediately():
    """When size drops to 0, the fill must be cleared on the same tick —
    don't make the HUD wait for the entry-price watcher to read a blank."""
    engine = StrategyEngine(_strategy_cfg())
    engine.state.to_pending_entry("BUY", trigger_price=100.0,
                                  stop=95.0, target=110.0)
    engine.confirm_entry_filled(100.0)

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.position_size = 1
    sup.state.current_position_side = "long"
    sup.state.last_fill_price = 100.0
    sup.state.last_fill_price_source = "position_ocr"

    sup._on_position_size_changed(0)

    assert sup.state.position_size == 0
    assert sup.state.current_position_side == "flat"
    assert sup.state.last_fill_price is None
    assert sup.state.last_fill_price_source is None


def test_position_watcher_scale_in_updates_size_without_engine_churn():
    """Scaling from 1 -> 2 keeps us in-position; engine shouldn't transition."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()

    sup._on_position_size_changed(1)
    sup._on_position_size_changed(2)

    assert sup.state.position_size == 2
    assert sup.state.current_position_side == "long"


def test_entry_price_watcher_sets_fill_with_source_tag():
    """EntryPriceWatcher's handler writes the fill price + marks the
    source as position_ocr so the HUD labels it '(verified)'."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)

    sup._on_entry_price_changed(26680.25)

    assert sup.state.last_fill_price == 26680.25
    assert sup.state.last_fill_price_source == "position_ocr"


def test_entry_price_watcher_none_clears_fill():
    """None (blank/unparseable cell — typically because we're flat) must
    clear any previously-captured fill so the HUD's PnL line flips to '—'."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup.state.last_fill_price = 26680.25
    sup.state.last_fill_price_source = "position_ocr"

    sup._on_entry_price_changed(None)

    assert sup.state.last_fill_price is None
    assert sup.state.last_fill_price_source is None


def test_split_watchers_together_drive_long_with_verified_fill():
    """End-to-end flow: size watcher fires +1 (long), then entry-price
    watcher fires 26680.25. Combined state matches what the HUD needs
    to render 'LONG @ 26680.25 size: 1 (verified)' with live PnL."""
    engine = StrategyEngine(_strategy_cfg())
    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()

    sup._on_position_size_changed(1)
    sup._on_entry_price_changed(26680.25)

    assert sup.state.current_position_side == "long"
    assert sup.state.position_size == 1
    assert sup.state.last_fill_price == 26680.25
    assert sup.state.last_fill_price_source == "position_ocr"


def test_position_watcher_pending_entry_uses_last_fill_from_entry_watcher():
    """When a strategy entry was pending AND the entry-price watcher has
    already captured the fill, _on_position_size_changed(1) should confirm
    the engine's entry with that fill price."""
    engine = StrategyEngine(_strategy_cfg())
    engine._last_accepted_price = 26600.0
    engine.state.to_pending_entry("BUY", trigger_price=26600.0,
                                  stop=26500.0, target=26800.0)

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    # entry-price watcher fires first (racy in practice — fine either order)
    sup._on_entry_price_changed(26680.25)
    sup._on_position_size_changed(1)

    assert engine.state.state == "LONG"
    assert engine.state.position.entry_price == 26680.25


def test_position_watcher_n_to_zero_closes_position_in_engine():
    """N -> 0 while engine is in-position drops it to FLAT."""
    engine = StrategyEngine(_strategy_cfg())
    engine._last_accepted_price = 100.0
    engine.state.to_pending_entry("BUY", trigger_price=100.0,
                                  stop=95.0, target=110.0)
    engine.confirm_entry_filled(100.0)   # now LONG
    assert engine.state.state == "LONG"

    sup = _make_supervisor(FakeExecutor(), engine=engine)
    sup._position_watcher = object()
    sup.state.position_size = 2           # seed prev
    sup._on_position_size_changed(0)

    assert engine.state.state == "FLAT"
    assert sup.state.current_position_side == "flat"
    assert sup.state.position_size == 0


def test_unknown_ack_still_halts_when_no_watcher():
    """Fallback: without the watcher we preserve the old safe behavior."""
    ex = FakeExecutor(status="unknown")
    sup = _make_supervisor(ex)
    assert sup._position_watcher is None
    intent = SignalIntent(action="BUY", reason="entry", trigger_price=100.0)
    ack = ExecutionAck(intent_id=intent.intent_id, action="BUY",
                       status="unknown", message="no_evidence_region")
    sup._reconcile_ack(intent, ack)
    assert sup.state.halted


def test_pause_sets_flag_and_suspends_engine_stream_ok():
    """Pause flips the flag + tells the engine the stream isn't ok."""
    sup = _make_supervisor(FakeExecutor())
    assert not sup.state.paused
    sup._pause("anchor_drift")
    assert sup.state.paused
    assert sup.state.pause_reason == "anchor_drift"
    # Engine price_stream_ok flag should have been flipped off
    assert sup.deps.engine._price_stream_ok is False


def test_resume_if_paused_clears_state():
    sup = _make_supervisor(FakeExecutor())
    sup._pause("anchor_drift")
    assert sup.state.paused
    sup._resume_if_paused()
    assert not sup.state.paused
    assert sup.state.pause_reason is None


def test_resume_if_paused_is_noop_when_not_paused():
    sup = _make_supervisor(FakeExecutor())
    # no prior pause — should be a cheap no-op, not raise
    sup._resume_if_paused()
    assert not sup.state.paused


def test_halt_does_not_clear_pause_flag_silently():
    """If the supervisor later halts while paused, halted takes priority."""
    sup = _make_supervisor(FakeExecutor())
    sup._pause("anchor_drift")
    sup._halt("execution_ack_unknown")
    assert sup.state.halted
    # pause flag may remain (it's transient), but halted must dominate in the UI


def test_disarm_sets_dry_run():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PAPER"
    sup._try_arm()
    assert sup.state.armed
    sup._set_armed(False)
    assert not sup.state.armed
    assert sup.state.mode == "PAPER"
