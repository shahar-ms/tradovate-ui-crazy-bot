"""
Live HUD demo: run a simulated trade-flow scenario against the real
floating HUD so you can SEE position, entry price, side, and PnL update
as a trade plays out. No real OCR, no real clicks, no calibration needed.

    python -m app.ui.demo_hud_trade

A small "Demo control" panel sits next to the HUD with one button per
scenario. Clicking a button schedules the scenario's events on QTimers
so each step is visible. The HUD repaints from the same UiState the
real bot uses, so what you see is exactly what an operator would see
during a live trade with these inputs.

Why this exists:
  - Visual sanity-check after touching position-watcher / PnL code.
  - Quick "what does a winning vs. losing trade actually look like?"
    walkthrough.
  - A reference test bed for trailing-stop / scale-out features once
    they ship.

Same scenarios are also covered synchronously (no Qt) by
tests/test_trade_flow_e2e.py — assertions there guard regressions.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from app.execution.models import ExecutionAck, ExecutionIntent
from app.models.common import Point, Region, ScreenMap
from app.models.config import BotConfig, StrategyConfig
from app.orchestrator.runtime_models import RuntimeState
from app.orchestrator.supervisor import Supervisor, SupervisorDeps
from app.orchestrator.trade_flow import TradeFlow
from app.strategy.engine import StrategyEngine
from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.theme import (BORDER, BROKEN_RED, INACTIVE_GRAY, OK_GREEN, PANEL,
                          PANEL_ALT, STYLESHEET, TEXT)
from app.ui.ui_state import UiState
from app.ui.widgets.floating_hud import FloatingHud
from app.utils.logging_utils import setup_logging

log = logging.getLogger(__name__)


# ============================================================
# Fake supervisor wiring (no bootstrap, no PriceStream, no OCR)
# ============================================================


class _NoopExecutor:
    """Stand-in for the real Executor. The demo never sends intents
    through it — TradeFlow drives the broker-side handlers directly —
    but Supervisor wants something with the right shape."""

    class _Cfg:
        dry_run = True

    def __init__(self):
        self.consecutive_unknown_acks = 0
        self.config = _NoopExecutor._Cfg()
        self.on_click = None

    def execute(self, intent: ExecutionIntent) -> ExecutionAck:
        return ExecutionAck(intent_id=intent.intent_id, action=intent.action,
                            status="ok", message="demo")

    def set_dry_run(self, dry_run: bool) -> None:
        self.config.dry_run = dry_run

    def close(self) -> None:
        pass


def _demo_screen_map() -> ScreenMap:
    """A throwaway screen_map. The demo doesn't actually click anywhere
    on screen (pyautogui.click is stubbed out in main()), so the exact
    coordinates are irrelevant — but they have to be valid Pydantic data."""
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


def _build_demo_supervisor() -> Supervisor:
    deps = SupervisorDeps(
        bot_cfg=BotConfig(preprocess_recipes=["gray_only"]),
        screen_map=_demo_screen_map(),
        executor=_NoopExecutor(),  # type: ignore[arg-type]
        engine=StrategyEngine(StrategyConfig()),
    )
    state = RuntimeState(mode="PAPER", armed=True)
    return Supervisor(deps=deps, state=state)


# ============================================================
# Scenarios — same shapes as test_trade_flow_e2e.py
# ============================================================


@dataclass
class _Step:
    """One scheduled event in a scenario. Time is cumulative ms from
    the scenario's start so we can fire them all at once via QTimer."""
    t_ms: int
    label: str
    fn: Callable[[TradeFlow], None]


def _scenario_long_win() -> tuple[str, list[_Step]]:
    return "Long winning trade (1 contract +20.5 pts → +$41)", [
        _Step(0,    "tick 26680.00 (pre-trade)",   lambda f: f.tick(26680.00)),
        _Step(800,  "HUD click: BUY",              lambda f: f.hud_click("BUY")),
        _Step(1700, "broker fills LONG 1 @ 26680", lambda f: f.open("long", 26680.00, 1)),
        _Step(3000, "tick 26685.00",               lambda f: f.tick(26685.00)),
        _Step(4200, "tick 26690.00",               lambda f: f.tick(26690.00)),
        _Step(5400, "tick 26695.00",               lambda f: f.tick(26695.00)),
        _Step(6600, "tick 26700.50 (peak)",        lambda f: f.tick(26700.50)),
        _Step(8000, "HUD click: CANCEL ALL",       lambda f: f.hud_click("CANCEL_ALL")),
        _Step(8800, "broker closes (size→0)",      lambda f: f.close()),
    ]


def _scenario_short_loss() -> tuple[str, list[_Step]]:
    return "Short losing trade (2 contracts −10.25 pts → −$41)", [
        _Step(0,    "tick 26700.00",                lambda f: f.tick(26700.00)),
        _Step(800,  "HUD click: SELL",              lambda f: f.hud_click("SELL")),
        _Step(1700, "broker fills SHORT 2 @ 26700", lambda f: f.open("short", 26700.00, 2)),
        _Step(3000, "tick 26705.00 (adverse)",      lambda f: f.tick(26705.00)),
        _Step(4200, "tick 26708.00",                lambda f: f.tick(26708.00)),
        _Step(5400, "tick 26710.25 (drawdown)",     lambda f: f.tick(26710.25)),
        _Step(6800, "HUD click: CANCEL ALL",        lambda f: f.hud_click("CANCEL_ALL")),
        _Step(7600, "broker closes (size→0)",       lambda f: f.close()),
    ]


def _scenario_side_flip() -> tuple[str, list[_Step]]:
    return "Side flip (long → short, no flat between)", [
        _Step(0,    "tick 26680.00",                lambda f: f.tick(26680.00)),
        _Step(800,  "HUD click: BUY",               lambda f: f.hud_click("BUY")),
        _Step(1700, "broker fills LONG 1 @ 26680",  lambda f: f.open("long", 26680.00, 1)),
        _Step(3200, "tick 26690.00 (in profit)",    lambda f: f.tick(26690.00)),
        _Step(4500, "HUD click: SELL",              lambda f: f.hud_click("SELL")),
        _Step(5400, "broker reverses to SHORT 1",   lambda f: f.scale(-1, new_entry=26690.00)),
        _Step(6700, "tick 26685.00 (short profit)", lambda f: f.tick(26685.00)),
        _Step(8000, "HUD click: CANCEL ALL",        lambda f: f.hud_click("CANCEL_ALL")),
        _Step(8800, "broker closes (size→0)",       lambda f: f.close()),
    ]


def _scenario_scale_in() -> tuple[str, list[_Step]]:
    return "Scale-in (1 → 2 contracts, USD PnL doubles)", [
        _Step(0,    "tick 26680.00",                  lambda f: f.tick(26680.00)),
        _Step(800,  "HUD click: BUY",                 lambda f: f.hud_click("BUY")),
        _Step(1700, "broker fills LONG 1 @ 26680",    lambda f: f.open("long", 26680.00, 1)),
        _Step(3000, "tick 26690.00 (+$20)",           lambda f: f.tick(26690.00)),
        _Step(4500, "scale to 2 contracts",           lambda f: f.scale(2)),
        _Step(5800, "tick 26695.00 (USD doubles)",    lambda f: f.tick(26695.00)),
        _Step(7100, "tick 26700.00",                  lambda f: f.tick(26700.00)),
        _Step(8400, "HUD click: CANCEL ALL",          lambda f: f.hud_click("CANCEL_ALL")),
        _Step(9200, "broker closes (size→0)",         lambda f: f.close()),
    ]


SCENARIOS: dict[str, Callable[[], tuple[str, list[_Step]]]] = {
    "long_win":    _scenario_long_win,
    "short_loss":  _scenario_short_loss,
    "side_flip":   _scenario_side_flip,
    "scale_in":    _scenario_scale_in,
}


# ============================================================
# Demo control panel — small floating window with scenario buttons
# ============================================================


class _DemoPanel(QWidget):
    def __init__(self, run_scenario: Callable[[str], None],
                 reset_state: Callable[[], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("HUD trade demo")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            f"QWidget {{ background-color: {PANEL}; color: {TEXT}; }}"
            f"QPushButton {{ background-color: {PANEL_ALT}; border: 1px solid {BORDER}; "
            f"   border-radius: 4px; padding: 8px 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: #2c3541; }}"
            f"QPushButton[role='primary'] {{ background-color: {OK_GREEN}; color: #0b0b0b; }}"
            f"QPushButton[role='danger']  {{ background-color: {BROKEN_RED}; color: white; }}"
            f"QPushButton[role='reset']   {{ background-color: {INACTIVE_GRAY}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("Trade-flow demo")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        root.addWidget(title)

        hint = QLabel("Click a scenario; watch the HUD play it out.")
        hint.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._status = QLabel("idle")
        self._status.setStyleSheet("color: #aaaaaa; font-size: 11px; padding: 4px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        for key, builder in SCENARIOS.items():
            label, _ = builder()
            btn = QPushButton(label)
            role = ("primary" if "winning" in label else
                    "danger"  if "losing" in label else
                    "")
            if role:
                btn.setProperty("role", role)
            # capture key by default arg to avoid the late-binding pitfall
            btn.clicked.connect(lambda _checked=False, k=key: run_scenario(k))
            root.addWidget(btn)

        reset = QPushButton("Reset (flatten + clear PnL)")
        reset.setProperty("role", "reset")
        reset.clicked.connect(reset_state)
        root.addWidget(reset)

        self.resize(360, 380)

    def set_status(self, text: str) -> None:
        self._status.setText(text)


# ============================================================
# Boot
# ============================================================


def _stub_pyautogui() -> None:
    """Replace pyautogui.click with a no-op so HUD click handlers never
    actually move the mouse during the demo. Production HUD clicks go
    through controller.hud_click which calls pyautogui.click internally;
    we keep that path live so the demo exercises the real code path,
    just without the side effect on the screen."""
    try:
        import pyautogui
        pyautogui.click = lambda *_a, **_kw: None  # type: ignore[assignment]
    except Exception:
        log.warning("pyautogui not importable — demo will skip the HUD click side effect")


def _enable_windows_dpi_awareness() -> None:
    """Match run_ui.py: declare per-monitor DPI awareness BEFORE Qt
    initializes its window subsystem. Silences the Qt warning
    'SetProcessDpiAwarenessContext() failed: The operation completed
    successfully.' that fires when Qt's default V2 awareness clashes
    with whatever the process inherited."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _run_scenario_on_flow(steps: list[_Step], flow: TradeFlow,
                          controller: UiController, hud: FloatingHud,
                          set_status: Callable[[str], None],
                          on_done: Callable[[], None]) -> None:
    """Schedule every step on QTimer.singleShot at its absolute t_ms.
    After each step we explicitly run the controller's poll AND the HUD's
    refresh so the screen update is instant instead of waiting up to
    ~480ms for both timers to fire on their own (controller poll 80ms +
    HUD refresh 400ms)."""
    def fire(step: _Step) -> None:
        step.fn(flow)
        # Push supervisor state -> UiState immediately, then repaint.
        controller._poll_once()
        hud._refresh_all()
        s = flow.latest
        set_status(
            f"t={step.t_ms}ms  {step.label}\n"
            f"   → side={s.side}  size={s.size}  "
            f"fill={s.fill}  last={s.last_price}  pnl_usd={s.pnl_usd}"
        )

    for step in steps:
        QTimer.singleShot(step.t_ms, lambda s=step: fire(s))

    last = steps[-1].t_ms if steps else 0
    def finish() -> None:
        controller._poll_once()
        hud._refresh_all()
        set_status(_done_status(flow))
        on_done()
    QTimer.singleShot(last + 600, finish)


def _done_status(flow: TradeFlow) -> str:
    pts, usd = flow.realized_pnl()
    if pts is None:
        return "scenario complete (no realized PnL recorded)"
    return f"DONE — realized {pts:+.2f} pts / {usd:+.2f} USD"


def _reset_demo_state(sup: Supervisor) -> None:
    """Flatten the simulated position so the HUD goes back to 'idle' look."""
    sup._on_position_size_changed(0)
    sup._on_entry_price_changed(None)
    sup.state.last_price = None
    sup.state.last_manual_click_action = None


def main(argv: list[str] | None = None) -> int:
    setup_logging(level="INFO")
    _enable_windows_dpi_awareness()   # must precede Qt init
    _stub_pyautogui()

    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Tradovate bot — trade-flow demo")
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(True)

    signals = AppSignals()
    ui_state = UiState()
    ui_state.calibration_loaded = True   # so the HUD's CAL chip shows green
    ui_state.mode = "PAPER"
    ui_state.armed = True                # so the HUD looks "live"
    ui_state.auto_enabled = False

    controller = UiController(signals=signals, state=ui_state)
    sup = _build_demo_supervisor()
    controller._supervisor = sup
    # Mirror what controller.start() would do for poll-driven UiState sync.
    controller._poll_timer.start()

    hud = FloatingHud(signals=signals, state=ui_state, controller=controller)
    hud.place_default()
    hud.show()

    # ---- demo control panel ---- #
    panel: _DemoPanel
    busy: dict[str, bool] = {"running": False}

    def run(scenario_key: str) -> None:
        if busy["running"]:
            panel.set_status("scenario in progress — wait or Reset")
            return
        if scenario_key not in SCENARIOS:
            panel.set_status(f"unknown scenario: {scenario_key}")
            return
        _reset_demo_state(sup)
        flow = TradeFlow(sup, controller=controller)
        label, steps = SCENARIOS[scenario_key]()
        panel.set_status(f"running: {label}")
        log.info("demo scenario start: %s", label)
        busy["running"] = True

        def done():
            busy["running"] = False
            log.info("demo scenario done")

        _run_scenario_on_flow(steps, flow, controller, hud,
                              panel.set_status, done)

    def reset() -> None:
        busy["running"] = False
        _reset_demo_state(sup)
        panel.set_status("reset — supervisor flat, ready for next scenario")

    panel = _DemoPanel(run_scenario=run, reset_state=reset)
    # Place it to the right of the HUD so both fit on a typical screen.
    panel.move(hud.x() + hud.width() + 30, hud.y())
    panel.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
