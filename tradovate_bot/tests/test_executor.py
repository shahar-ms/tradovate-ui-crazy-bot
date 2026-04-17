"""
Executor tests. We stub the guard, ack reader, and drivers so no real screen
capture or mouse clicks happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pytest

from app.execution.ack_reader import AckSignal
from app.execution.click_driver import RecordingClickDriver
from app.execution.executor import Executor
from app.execution.guards import GuardResult
from app.execution.hotkey_driver import RecordingHotkeyDriver
from app.execution.models import ExecutionConfig, ExecutionIntent, Hotkeys
from app.models.common import Point, Region, ScreenMap


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
        status_region=Region(left=1100, top=980, width=400, height=80),
    )


@dataclass
class FakeGuard:
    result: GuardResult = field(default_factory=lambda: GuardResult(True, similarity=0.95))
    calls: int = 0
    last_point: Optional[Point] = None

    def check(self, target_point: Optional[Point] = None) -> GuardResult:
        self.calls += 1
        self.last_point = target_point
        return self.result


@dataclass
class FakeAckReader:
    before_shape: tuple = (10, 10, 3)
    after_signal: AckSignal = field(default_factory=lambda: AckSignal("ok", "delta=0.10"))
    capture_calls: int = 0
    after_calls: int = 0

    def capture_before(self, action: str):
        self.capture_calls += 1
        return np.zeros(self.before_shape, dtype=np.uint8)

    def read_after(self, action: str, before):
        self.after_calls += 1
        return self.after_signal


def _executor(guard: FakeGuard, ack: FakeAckReader, *, dry_run: bool = False,
              hotkey_fallback: bool = False, hotkeys: Optional[Hotkeys] = None):
    click = RecordingClickDriver()
    hk = RecordingHotkeyDriver()
    cfg = ExecutionConfig(
        dry_run=dry_run,
        enable_hotkey_fallback=hotkey_fallback,
        hotkeys=hotkeys or Hotkeys(),
        ack_evidence_save=False,
    )
    ex = Executor(
        screen_map=_screen_map(),
        config=cfg,
        click_driver=click,
        hotkey_driver=hk,
        guard=guard,
        ack_reader=ack,
    )
    return ex, click, hk


def test_blocked_when_guard_fails():
    guard = FakeGuard(result=GuardResult(False, reason="anchor_mismatch:0.5"))
    ack = FakeAckReader()
    ex, click, _ = _executor(guard, ack)
    intent = ExecutionIntent(action="BUY", reason="test")
    out = ex.execute(intent)
    assert out.status == "blocked"
    assert "anchor_mismatch" in out.message
    assert click.calls == []           # no click
    assert ack.capture_calls == 0      # no ack attempt
    assert out.screen_guard_passed is False


def test_click_buy_ok():
    ex, click, _ = _executor(FakeGuard(), FakeAckReader())
    intent = ExecutionIntent(action="BUY", reason="entry")
    out = ex.execute(intent)
    assert out.status == "ok"
    assert click.calls == [_screen_map().buy_point]


def test_click_sell_ok():
    ex, click, _ = _executor(FakeGuard(), FakeAckReader())
    out = ex.execute(ExecutionIntent(action="SELL", reason="entry"))
    assert out.status == "ok"
    assert click.calls == [_screen_map().sell_point]


def test_click_cancel_all_ok():
    ex, click, _ = _executor(FakeGuard(), FakeAckReader())
    out = ex.execute(ExecutionIntent(action="CANCEL_ALL", reason="cleanup"))
    assert out.status == "ok"
    assert click.calls == [_screen_map().cancel_all_point]


def test_unknown_ack_propagates():
    ack = FakeAckReader(after_signal=AckSignal("unknown", "no_visible_change:delta=0.001"))
    ex, click, _ = _executor(FakeGuard(), ack)
    out = ex.execute(ExecutionIntent(action="BUY", reason="x"))
    assert out.status == "unknown"
    assert ex.consecutive_unknown_acks == 1


def test_consecutive_unknown_counter():
    ack = FakeAckReader(after_signal=AckSignal("unknown", "nope"))
    ex, _, _ = _executor(FakeGuard(), ack)
    ex.execute(ExecutionIntent(action="BUY", reason="a"))
    ex.execute(ExecutionIntent(action="SELL", reason="b"))
    assert ex.consecutive_unknown_acks == 2


def test_dry_run_clicks_but_status_is_ok():
    ex, click, _ = _executor(FakeGuard(), FakeAckReader(), dry_run=True)
    out = ex.execute(ExecutionIntent(action="BUY", reason="dry"))
    assert out.status == "ok"
    assert out.message == "dry_run"
    # dry-run still records the click target on the recorder (so we can inspect),
    # but never touches the OS because the driver is the RecordingClickDriver.
    assert click.calls == [_screen_map().buy_point]


def test_hotkey_mode_used_when_enabled():
    hk = Hotkeys(buy="f9", sell="f10", cancel_all="f12")
    ex, click, hkrec = _executor(FakeGuard(), FakeAckReader(),
                                 hotkey_fallback=True, hotkeys=hk)
    out = ex.execute(ExecutionIntent(action="BUY", reason="hk"))
    assert out.status == "ok"
    assert click.calls == []
    assert hkrec.calls == ["f9"]
    assert out.mode == "hotkey"


def test_point_out_of_bounds_blocked():
    """If a caller somehow asks to click outside, guard must stop it.
    Use the real guard logic by wrapping a guard that delegates to the model."""
    class BoundsCheckingGuard:
        def __init__(self):
            self.last_point = None
            self.calls = 0

        def check(self, target_point=None):
            self.calls += 1
            self.last_point = target_point
            sm = _screen_map()
            if target_point and not sm.point_in_screen(target_point):
                return GuardResult(False, reason="point_out_of_bounds")
            return GuardResult(True, similarity=1.0)

    guard = BoundsCheckingGuard()
    ex, click, _ = _executor(guard, FakeAckReader())
    # Force an invalid point by monkey-patching the screen map's buy_point
    ex.screen_map = ex.screen_map.model_copy(update={"buy_point": Point(x=9999, y=9999)})
    out = ex.execute(ExecutionIntent(action="BUY", reason="oob"))
    assert out.status == "blocked"
    assert "point_out_of_bounds" in out.message
    assert click.calls == []


def test_driver_exception_becomes_failed_ack():
    class BrokenDriver:
        def click_point(self, point):
            raise RuntimeError("click failed")

    ex, _, _ = _executor(FakeGuard(), FakeAckReader())
    ex.click_driver = BrokenDriver()
    out = ex.execute(ExecutionIntent(action="BUY", reason="x"))
    assert out.status == "failed"
    assert "driver_exception" in out.message
