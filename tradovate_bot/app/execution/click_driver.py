"""
Low-level mouse click driver. Pluggable: the Protocol below lets tests
inject a recording driver instead of hitting the real OS.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from app.models.common import Point

log = logging.getLogger(__name__)


class ClickDriver(Protocol):
    def click_point(self, point: Point) -> None: ...


class PyAutoGUIClickDriver:
    """Real driver, backed by pyautogui. Import is lazy so tests don't need it."""

    def __init__(self, move_duration_ms: int = 80,
                 pre_click_delay_ms: int = 40,
                 post_click_delay_ms: int = 120,
                 fail_safe: bool = True):
        import pyautogui
        self._pag = pyautogui
        pyautogui.FAILSAFE = fail_safe
        pyautogui.PAUSE = 0.0  # we manage delays explicitly
        self.move_duration = move_duration_ms / 1000.0
        self.pre_delay = pre_click_delay_ms / 1000.0
        self.post_delay = post_click_delay_ms / 1000.0

    def click_point(self, point: Point) -> None:
        self._pag.moveTo(point.x, point.y, duration=self.move_duration)
        if self.pre_delay > 0:
            time.sleep(self.pre_delay)
        self._pag.click()
        if self.post_delay > 0:
            time.sleep(self.post_delay)


class RecordingClickDriver:
    """Test double: records every click instead of executing it."""

    def __init__(self) -> None:
        self.calls: list[Point] = []

    def click_point(self, point: Point) -> None:
        self.calls.append(point)
