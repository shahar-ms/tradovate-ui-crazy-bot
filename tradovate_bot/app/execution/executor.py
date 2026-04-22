"""
Executor: receive an ExecutionIntent, run guard, perform exactly one UI action
(click or hotkey), read ack, return an ExecutionAck.

Safety rules enforced here:
  - single-flight execution (threading.Lock)
  - guard-first
  - no hidden retries
  - clear ack states: ok / failed / unknown / blocked
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from app.capture.screen_capture import ScreenCapture
from app.models.common import Point, ScreenMap
from app.utils import image_utils as iu
from app.utils import paths
from app.utils.time_utils import now_ms, session_id

from .ack_reader import AckReader
from .click_driver import ClickDriver, PyAutoGUIClickDriver, RecordingClickDriver
from .guards import ScreenGuard
from .hotkey_driver import HotkeyDriver, PyAutoGUIHotkeyDriver, RecordingHotkeyDriver
from .models import ActionT, ExecutionAck, ExecutionConfig, ExecutionIntent

log = logging.getLogger(__name__)


def _build_default_click_driver(config: ExecutionConfig) -> ClickDriver:
    return PyAutoGUIClickDriver(
        move_duration_ms=config.move_duration_ms,
        pre_click_delay_ms=config.pre_click_delay_ms,
        post_click_delay_ms=config.post_click_delay_ms,
    )


class Executor:
    def __init__(
        self,
        screen_map: ScreenMap,
        config: ExecutionConfig,
        click_driver: Optional[ClickDriver] = None,
        hotkey_driver: Optional[HotkeyDriver] = None,
        guard: Optional[ScreenGuard] = None,
        ack_reader: Optional[AckReader] = None,
    ):
        self.screen_map = screen_map
        self.config = config
        self._lock = threading.Lock()

        # Track whether drivers were caller-injected so set_dry_run() knows
        # whether it's allowed to swap them. Tests inject a RecordingDriver
        # and rely on it never being replaced; production injects nothing
        # and we manage the real/recording swap ourselves on arm/disarm.
        self._click_driver_injected = click_driver is not None
        self._hotkey_driver_injected = hotkey_driver is not None

        # Drivers: for dry-run we default to RecordingClickDriver (no OS interaction).
        if click_driver is not None:
            self.click_driver = click_driver
        elif config.dry_run:
            self.click_driver = RecordingClickDriver()
        else:
            self.click_driver = _build_default_click_driver(config)

        if hotkey_driver is not None:
            self.hotkey_driver = hotkey_driver
        elif config.dry_run or not config.enable_hotkey_fallback:
            self.hotkey_driver = RecordingHotkeyDriver()
        else:
            self.hotkey_driver = PyAutoGUIHotkeyDriver()

        # Shared capture for guard + ack reader (one mss session).
        self._shared_capture: Optional[ScreenCapture] = None
        if guard is None or ack_reader is None:
            self._shared_capture = ScreenCapture(screen_map.monitor_index)

        self.guard = guard or ScreenGuard(
            screen_map=screen_map,
            anchor_threshold=config.anchor_match_threshold,
            capture=self._shared_capture,
        )
        self.ack_reader = ack_reader or AckReader(
            screen_map=screen_map,
            capture=self._shared_capture,
        )

        self.consecutive_unknown_acks = 0

        # Optional callback invoked after every real (non-dry-run) click.
        # The UI layer sets this to flash a marker at the click point.
        self.on_click: Optional[Callable[[int, int], None]] = None

    # ---- public API ---- #

    def execute(self, intent: ExecutionIntent) -> ExecutionAck:
        with self._lock:
            return self._execute_locked(intent)

    def set_dry_run(self, dry_run: bool) -> None:
        """Flip live/dry mode and swap in the correct driver for the new
        mode. The Executor is constructed with `config.dry_run=True` (safe
        default), which wires up a RecordingClickDriver that does not touch
        the OS — if we only toggled `config.dry_run` on arm, HUD clicks
        would silently land in the recording buffer instead of Tradovate.
        Injected drivers (tests) are never swapped."""
        with self._lock:
            self.config.dry_run = dry_run
            if not self._click_driver_injected:
                if dry_run:
                    if not isinstance(self.click_driver, RecordingClickDriver):
                        self.click_driver = RecordingClickDriver()
                else:
                    if isinstance(self.click_driver, RecordingClickDriver):
                        self.click_driver = _build_default_click_driver(self.config)
            if not self._hotkey_driver_injected:
                want_recording = dry_run or not self.config.enable_hotkey_fallback
                if want_recording:
                    if not isinstance(self.hotkey_driver, RecordingHotkeyDriver):
                        self.hotkey_driver = RecordingHotkeyDriver()
                else:
                    if isinstance(self.hotkey_driver, RecordingHotkeyDriver):
                        self.hotkey_driver = PyAutoGUIHotkeyDriver()

    def close(self) -> None:
        if self._shared_capture is not None:
            self._shared_capture.close()

    # ---- internals ---- #

    def _execute_locked(self, intent: ExecutionIntent) -> ExecutionAck:
        action = intent.action
        target_point = self._point_for_action(action)

        # Even hotkey mode wants the guard to pass.
        guard_result = self.guard.check(target_point=target_point if target_point else None)
        log.info("intent=%s id=%s reason=%s %s",
                 action, intent.intent_id, intent.reason, guard_result.as_message())
        if not guard_result.ok:
            return ExecutionAck(
                intent_id=intent.intent_id,
                action=action,
                status="blocked",
                message=guard_result.reason or "guard_blocked",
                screen_guard_passed=False,
                mode=self._mode_for_action(action),
            )

        mode = self._mode_for_action(action)

        if self.config.dry_run:
            log.warning("DRY-RUN: would perform %s via %s (point=%s)",
                        action, mode, target_point.model_dump() if target_point else None)
            if mode == "click" and target_point is not None:
                self.click_driver.click_point(target_point)
            elif mode == "hotkey":
                combo = self._hotkey_for_action(action) or ""
                self.hotkey_driver.send(combo)
            return ExecutionAck(
                intent_id=intent.intent_id,
                action=action,
                status="ok",
                message="dry_run",
                screen_guard_passed=True,
                mode=mode,
            )

        # ARMED / live path
        before = self.ack_reader.capture_before(action)
        try:
            if mode == "click":
                if target_point is None:
                    return self._failed(intent, mode, "no_click_point_for_action")
                self.click_driver.click_point(target_point)
                if self.on_click is not None:
                    try:
                        self.on_click(target_point.x, target_point.y)
                    except Exception:
                        log.debug("on_click callback raised", exc_info=True)
            else:
                combo = self._hotkey_for_action(action)
                if not combo:
                    return self._failed(intent, mode, "no_hotkey_for_action")
                self.hotkey_driver.send(combo)
        except Exception as e:
            log.exception("driver raised on %s: %s", action, e)
            return self._failed(intent, mode, f"driver_exception:{e}")

        signal = self.ack_reader.read_after(action, before)
        evidence_path = self._maybe_save_evidence(signal.evidence_image, intent.intent_id)

        if signal.status == "ok":
            self.consecutive_unknown_acks = 0
            ack_status = "ok"
        elif signal.status == "failed":
            self.consecutive_unknown_acks = 0
            ack_status = "failed"
        else:
            self.consecutive_unknown_acks += 1
            ack_status = "unknown"

        return ExecutionAck(
            intent_id=intent.intent_id,
            action=action,
            status=ack_status,
            message=signal.message,
            screen_guard_passed=True,
            evidence_path=evidence_path,
            mode=mode,
            fill_price=signal.fill_price,
            fill_price_confidence=signal.fill_price_confidence,
            fill_price_source=signal.fill_price_source,
        )

    def reload_screen_map(self, new_map: ScreenMap) -> None:
        """
        Swap in a freshly-saved ScreenMap (e.g. after the operator re-calibrates
        from the HUD's Setup dialog). Rebuilds the ScreenGuard and AckReader so
        they see the new regions + anchor reference.
        """
        with self._lock:
            self.screen_map = new_map
            # rebuild guard + ack reader; close old shared capture first
            if self._shared_capture is not None:
                try:
                    self._shared_capture.close()
                except Exception:
                    pass
                self._shared_capture = ScreenCapture(new_map.monitor_index)
            self.guard = ScreenGuard(
                screen_map=new_map,
                anchor_threshold=self.config.anchor_match_threshold,
                capture=self._shared_capture,
            )
            self.ack_reader = AckReader(
                screen_map=new_map,
                capture=self._shared_capture,
            )
            log.info("executor: screen_map reloaded (%dx%d, monitor=%d)",
                     new_map.screen_width, new_map.screen_height,
                     new_map.monitor_index)

    def _failed(self, intent: ExecutionIntent, mode, reason: str) -> ExecutionAck:
        return ExecutionAck(
            intent_id=intent.intent_id,
            action=intent.action,
            status="failed",
            message=reason,
            screen_guard_passed=True,
            mode=mode,
        )

    def _point_for_action(self, action: ActionT) -> Optional[Point]:
        if action == "BUY":
            return self.screen_map.buy_point
        if action == "SELL":
            return self.screen_map.sell_point
        if action == "CANCEL_ALL":
            return self.screen_map.cancel_all_point
        return None

    def _hotkey_for_action(self, action: ActionT) -> Optional[str]:
        hk = self.config.hotkeys
        if action == "BUY":
            return hk.buy
        if action == "SELL":
            return hk.sell
        if action == "CANCEL_ALL":
            return hk.cancel_all
        return None

    def _mode_for_action(self, action: ActionT) -> str:
        if self.config.enable_hotkey_fallback and self._hotkey_for_action(action):
            return "hotkey"
        return "click"

    def _maybe_save_evidence(self, img, intent_id: str) -> Optional[str]:
        if img is None or not self.config.ack_evidence_save:
            return None
        try:
            out = paths.screenshots_dir() / "exec_evidence" / f"{session_id()}_{intent_id}.png"
            iu.save_png(img, out)
            return str(out.relative_to(paths.project_root())).replace("\\", "/")
        except Exception as e:
            log.debug("evidence save failed: %s", e)
            return None
