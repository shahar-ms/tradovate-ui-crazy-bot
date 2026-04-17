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


def test_arm_requires_paper_or_armed_mode():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PRICE_DEBUG"
    sup._try_arm()
    assert not sup.state.armed  # cannot arm from PRICE_DEBUG

    sup.state.mode = "PAPER"
    sup._try_arm()
    assert sup.state.armed
    assert sup.state.mode == "ARMED"


def test_arm_blocked_when_halted():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PAPER"
    sup._halt("x")
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


def test_disarm_sets_dry_run():
    sup = _make_supervisor(FakeExecutor())
    sup.state.mode = "PAPER"
    sup._try_arm()
    assert sup.state.armed
    sup._set_armed(False)
    assert not sup.state.armed
    assert sup.state.mode == "PAPER"
