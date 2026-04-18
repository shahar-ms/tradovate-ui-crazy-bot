"""
Getting Started — guided 3-step walkthrough.

The user can't get lost: each step has a clear status (not-started / in-progress /
done) and a single 'Do this now' button. The button for the next step only
enables when the previous step is green.

Step 1 — Calibrate (green when screen_map.json exists AND offline validator passes).
Step 2 — See price flowing (green when health=ok AND >= 10 accepted ticks).
Step 3 — Paper mode (green when at least one signal intent has been emitted).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from app.calibration.validator import validate_calibration
from app.ui.app_signals import AppSignals
from app.ui.controller import UiController
from app.ui.theme import (BORDER, BROKEN_RED, DEGRADED_YELLOW, INACTIVE_GRAY, OK_GREEN,
                          PANEL, TEXT, TEXT_MUTED)
from app.ui.ui_state import UiState
from app.utils import paths

log = logging.getLogger(__name__)


@dataclass
class StepStatus:
    state: str          # "done" | "active" | "pending"
    headline: str       # short status
    detail: str = ""    # longer helpful text

    @property
    def is_done(self) -> bool:
        return self.state == "done"


MIN_TICKS_TO_PASS = 10


class StepCard(QWidget):
    """One step in the wizard."""

    def __init__(self, number: int, title: str, description: str,
                 action_label: str, on_action: Callable[[], None],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_action = on_action

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # title row
        row1 = QHBoxLayout()
        row1.setSpacing(12)

        self.number_badge = QLabel(str(number))
        self.number_badge.setFixedSize(34, 34)
        self.number_badge.setAlignment(Qt.AlignCenter)
        row1.addWidget(self.number_badge)

        title_col = QVBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.status_lbl = QLabel("waiting")
        self.status_lbl.setStyleSheet("font-size: 11px;")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.status_lbl)
        row1.addLayout(title_col, 1)

        self.action_btn = QPushButton(action_label)
        self.action_btn.setMinimumHeight(36)
        self.action_btn.setMinimumWidth(180)
        self.action_btn.clicked.connect(lambda: on_action())
        row1.addWidget(self.action_btn)

        outer.addLayout(row1)

        # description
        self.desc_lbl = QLabel(description)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        outer.addWidget(self.desc_lbl)

        # detail (live status from the backend)
        self.detail_lbl = QLabel("")
        self.detail_lbl.setWordWrap(True)
        self.detail_lbl.setStyleSheet("font-size: 11px;")
        outer.addWidget(self.detail_lbl)

        # default visual state = pending
        self._apply_visual("pending")

    def set_status(self, status: StepStatus, active: bool,
                   override_action_label: Optional[str] = None) -> None:
        effective = status.state
        if status.state != "done" and active:
            effective = "active"
        if status.state != "done" and not active:
            effective = "pending"
        self._apply_visual(effective)
        self.status_lbl.setText(status.headline)
        self.detail_lbl.setText(status.detail)
        self.action_btn.setEnabled(active or status.is_done)
        if override_action_label:
            self.action_btn.setText(override_action_label)

    def _apply_visual(self, effective: str) -> None:
        colors = {
            "done":    OK_GREEN,
            "active":  DEGRADED_YELLOW,
            "pending": INACTIVE_GRAY,
        }
        c = colors.get(effective, INACTIVE_GRAY)
        self.setStyleSheet(
            f"StepCard {{ background-color: {PANEL}; "
            f"border: 1px solid {BORDER}; border-left: 4px solid {c}; "
            f"border-radius: 6px; }}"
        )
        self.number_badge.setStyleSheet(
            f"background-color: {c}; color: #101010; "
            f"border-radius: 17px; font-weight: 800; font-size: 14px;"
        )
        self.status_lbl.setStyleSheet(f"font-size: 11px; color: {c}; font-weight: 600;")


class GettingStartedPage(QWidget):
    NAV_INDEX_CALIBRATION = 2   # set by MainWindow wiring; fallback default

    def __init__(self, signals: AppSignals, state: UiState, controller: UiController,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.state = state
        self.controller = controller
        self._active_step: int = 1

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(14)

        # hero
        hero_title = QLabel("Getting started")
        hero_title.setStyleSheet("font-size: 22px; font-weight: 700;")
        root.addWidget(hero_title)

        hero_sub = QLabel(
            "Three steps to price automation. Complete them in order — the next "
            "button unlocks when the previous step goes green."
        )
        hero_sub.setWordWrap(True)
        hero_sub.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        root.addWidget(hero_sub)

        # step 1
        self.step1 = StepCard(
            number=1,
            title="Calibrate the screen",
            description=(
                "Mark the price rectangle, the anchor region, and the Buy/Sell/Cancel "
                "click points on a screenshot of your Tradovate layout. The bot uses "
                "these coordinates forever."
            ),
            action_label="Open calibration",
            on_action=self._go_calibrate,
        )
        root.addWidget(self.step1)

        # step 2
        self.step2 = StepCard(
            number=2,
            title="See the price flowing",
            description=(
                "Start Price Debug mode. The bot captures the price region, OCRs it, "
                "and publishes validated ticks. No strategy, no clicks — just confirm "
                "the OCR is reading the correct number."
            ),
            action_label="Start Price Debug",
            on_action=self._start_price_debug,
        )
        root.addWidget(self.step2)

        # step 3
        self.step3 = StepCard(
            number=3,
            title="Run paper mode",
            description=(
                "Paper mode runs the full strategy (micro-bars, level detection, "
                "sweep entry rules) against the live price, but never clicks. The "
                "step completes when the engine emits its first BUY/SELL/CANCEL "
                "intent — that proves the strategy plumbing is alive."
            ),
            action_label="Start Paper Mode",
            on_action=self._start_paper_mode,
        )
        root.addWidget(self.step3)

        # footer
        root.addStretch(1)
        footer = QLabel(
            "Tip: open the Logs page to watch events, or the Dashboard for live "
            "panels. Ctrl+Shift+H toggles a small always-on-top HUD."
        )
        footer.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        footer.setWordWrap(True)
        root.addWidget(footer)

        # refresh loop
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    # ---- refresh ---- #

    def _refresh(self) -> None:
        s1 = self._step1_status()
        s2 = self._step2_status()
        s3 = self._step3_status()

        # the "active" step is the first non-done one
        active = 1
        for i, s in enumerate([s1, s2, s3], start=1):
            if not s.is_done:
                active = i
                break
            active = i + 1  # past all
        self._active_step = min(active, 3)

        # step 1 button label reflects whether calibration already exists
        step1_btn_label = "Open calibration" if not s1.is_done else "Re-calibrate"
        step2_btn_label = "Start Price Debug" if self.state.mode != "PRICE_DEBUG" else "Restart Price Debug"
        step3_btn_label = "Start Paper Mode" if self.state.mode != "PAPER" else "Restart Paper Mode"

        self.step1.set_status(s1, active=(self._active_step == 1),
                              override_action_label=step1_btn_label)
        self.step2.set_status(s2, active=(self._active_step == 2 and s1.is_done),
                              override_action_label=step2_btn_label)
        self.step3.set_status(s3, active=(self._active_step == 3 and s2.is_done),
                              override_action_label=step3_btn_label)

    def _step1_status(self) -> StepStatus:
        sm_path = paths.screen_map_path()
        if not sm_path.exists():
            return StepStatus(
                state="pending",
                headline="not calibrated yet",
                detail="No screen_map.json. Click 'Open calibration' and mark all required items.",
            )
        try:
            report = validate_calibration(offline=True)
        except Exception as e:
            return StepStatus(
                state="pending",
                headline="validator error",
                detail=f"Validator raised: {e}",
            )
        if report.ready:
            return StepStatus(
                state="done",
                headline="calibrated ✓",
                detail=f"screen_map.json valid. Monitor {self.state.monitor_index}, "
                       f"{self.state.screen_size[0]}x{self.state.screen_size[1]}.",
            )
        fail_line = next((l for l in report.lines if l.startswith("[FAIL]")),
                         "validation failed")
        return StepStatus(
            state="pending",
            headline="calibration invalid",
            detail=f"{fail_line.strip()}  — re-open the calibration page.",
        )

    def _step2_status(self) -> StepStatus:
        s = self.state
        if s.mode == "DISCONNECTED":
            return StepStatus(
                state="pending",
                headline="bot not running",
                detail="Click 'Start Price Debug' to launch the capture loop.",
            )
        if s.mode in ("PRICE_DEBUG", "PAPER", "ARMED"):
            accepted = s.accepted_tick_count
            health = s.price_stream_health
            if accepted >= MIN_TICKS_TO_PASS and health == "ok":
                return StepStatus(
                    state="done",
                    headline="price flowing ✓",
                    detail=f"{accepted} accepted ticks, health={health}. "
                           f"Last price: {s.last_price if s.last_price is not None else '—'}",
                )
            return StepStatus(
                state="active",
                headline=f"{accepted}/{MIN_TICKS_TO_PASS} accepted ticks  (health={health})",
                detail=(
                    "Tradovate must be visible under the calibrated regions. "
                    "If ticks aren't accumulating, check that the price region shows "
                    "clean price digits and that OCR confidence is high on the Dashboard."
                    if accepted < MIN_TICKS_TO_PASS else
                    f"Stream health is {health} — if it stays degraded, re-calibrate."
                ),
            )
        # HALTED or CALIBRATION
        return StepStatus(
            state="pending",
            headline=f"mode={s.mode}",
            detail="Bot halted. Resolve halt from the Run control page, then restart.",
        )

    def _step3_status(self) -> StepStatus:
        s = self.state
        if s.mode not in ("PAPER", "ARMED"):
            return StepStatus(
                state="pending",
                headline="paper mode not running",
                detail="Step 2 first, then click 'Start Paper Mode' to run the strategy.",
            )
        if s.signals_emitted_count >= 1:
            return StepStatus(
                state="done",
                headline=f"{s.signals_emitted_count} signals emitted ✓",
                detail="Strategy is alive. You're ready to move on to supervised ARMED runs "
                       "from the Run control page — no rush.",
            )
        return StepStatus(
            state="active",
            headline="waiting for a sweep setup",
            detail=(
                "Paper mode is running. The sweep/failed-breakout strategy needs a "
                "level to form (min_touches) and then a break-and-return around it. "
                "This can take minutes to hours depending on the market. "
                "Watch the Logs page for bar-close events."
            ),
        )

    # ---- actions ---- #

    def _go_calibrate(self) -> None:
        w = self.window()
        idx = getattr(w, "_calibration_index", None)
        if idx is not None and hasattr(w, "go_to"):
            w.go_to(idx)
        elif hasattr(w, "go_to"):
            w.go_to(self.NAV_INDEX_CALIBRATION)

    def _start_price_debug(self) -> None:
        s1 = self._step1_status()
        if not s1.is_done:
            QMessageBox.information(
                self, "Calibrate first",
                "Step 1 isn't complete yet. Calibrate the screen before starting "
                "the bot — otherwise it doesn't know where to look."
            )
            return
        if self.controller.is_running():
            self.controller.stop()
        err = self.controller.start(mode="PRICE_DEBUG", armed=False)
        if err:
            QMessageBox.critical(self, "Start failed", err)

    def _start_paper_mode(self) -> None:
        s2 = self._step2_status()
        if not s2.is_done:
            QMessageBox.information(
                self, "Price not flowing yet",
                "Step 2 isn't complete. Let Price Debug accumulate at least "
                f"{MIN_TICKS_TO_PASS} accepted ticks with health=ok before starting "
                "the strategy."
            )
            return
        if self.controller.is_running():
            self.controller.stop()
        err = self.controller.start(mode="PAPER", armed=False)
        if err:
            QMessageBox.critical(self, "Start failed", err)
