"""
Optional hotkey driver. Lets the executor send keyboard shortcuts instead of
clicking. Not used by default in v1 — click-based execution is primary.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

log = logging.getLogger(__name__)


class HotkeyDriver(Protocol):
    def send(self, combo: str) -> None: ...


class PyAutoGUIHotkeyDriver:
    def __init__(self, post_delay_ms: int = 80):
        import pyautogui
        self._pag = pyautogui
        self.post_delay = post_delay_ms / 1000.0

    def send(self, combo: str) -> None:
        keys = [k.strip() for k in combo.split("+") if k.strip()]
        if not keys:
            return
        if len(keys) == 1:
            self._pag.press(keys[0])
        else:
            self._pag.hotkey(*keys)
        if self.post_delay > 0:
            time.sleep(self.post_delay)


class RecordingHotkeyDriver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, combo: str) -> None:
        self.calls.append(combo)
