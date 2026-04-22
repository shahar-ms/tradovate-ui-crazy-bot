"""
Low-level mouse click driver. Pluggable: the Protocol below lets tests
inject a recording driver instead of hitting the real OS.

On Windows we use SendInput (the documented synthetic-input API) rather
than the legacy mouse_event. Some Chromium builds silently drop
mouse_event clicks when the target tab isn't foreground — Tradovate is
hosted in Chrome, and that exact symptom shows up as "mouse visibly
moves but nothing happens" in the app. SendInput doesn't have that issue.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Protocol

from app.models.common import Point
from app.utils import paths

log = logging.getLogger(__name__)

# On Windows, delegate the actual click to a short-lived subprocess. This
# matches the setup of the standalone click_test.py the user confirmed
# works, and side-steps whatever state in our Qt host process is causing
# in-process SendInput events to not reach Chrome.
_USE_SUBPROCESS_HELPER = sys.platform == "win32"


class ClickDriver(Protocol):
    def click_point(self, point: Point) -> None: ...


class PyAutoGUIClickDriver:
    """Real driver. Uses pyautogui for move (smooth glide) + SendInput for
    the actual click on Windows; falls back to pyautogui.click() elsewhere."""

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
        if sys.platform == "win32":
            _wireup_win32()
            self._send_input = _sendinput_click
            self._bring_to_front = _bring_to_front
        else:
            self._send_input = None
            self._bring_to_front = None

    def click_point(self, point: Point) -> None:
        _force_foreground_at(point.x, point.y)
        self._pag.moveTo(point.x, point.y, duration=self.move_duration)
        if self.pre_delay > 0:
            time.sleep(self.pre_delay)
        self._pag.click()
        if self.post_delay > 0:
            time.sleep(self.post_delay)


def _force_foreground_at(x: int, y: int) -> None:
    """Minimize-then-restore the window under (x, y). This is the classic
    Windows workaround for making a window foreground when SetForegroundWindow
    is being vetoed by the foreground-lock — restore is NEVER vetoed."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    u.WindowFromPoint.argtypes = (POINT,)
    u.WindowFromPoint.restype = wintypes.HWND
    u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    u.GetAncestor.restype = wintypes.HWND
    u.GetForegroundWindow.restype = wintypes.HWND
    u.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)

    hwnd = u.WindowFromPoint(POINT(x, y))
    if not hwnd:
        return
    top = u.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT = 2
    if u.GetForegroundWindow() == top:
        return
    u.ShowWindow(top, 6)   # SW_MINIMIZE
    time.sleep(0.02)
    u.ShowWindow(top, 9)   # SW_RESTORE
    time.sleep(0.10)


# --- Windows SendInput + foreground management --- #
#
# Everything below is lazily wired up by _wireup_win32() so non-Windows hosts
# (CI, tests) don't touch any Win32 API.

_sendinput_click = None
_bring_to_front = None
_WIN32_READY = False


def _wireup_win32() -> None:
    """Build and memoize the Win32-only SendInput click + foreground helper.
    Called exactly once per process. All Win32 API bindings live here so
    import-time doesn't blow up on Linux/macOS."""
    global _sendinput_click, _bring_to_front, _WIN32_READY
    if _WIN32_READY:
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    # The real Windows INPUT union contains all three. All INPUT records in
    # a single SendInput array are the same size (= max(member sizes)),
    # which matters because the caller passes sizeof(INPUT) and the OS
    # strides by that to iterate. Keep mi, ki, and hi all present so size
    # matches what Windows expects regardless of which arm we fill.
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("_u",)
        _fields_ = [("type", wintypes.DWORD), ("_u", _INPUT_UNION)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.WindowFromPoint.argtypes = (POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = (
        wintypes.DWORD, wintypes.DWORD, wintypes.BOOL,
    )
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    # --- constants --- #
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12  # Alt
    GA_ROOT = 2
    SW_SHOW = 5
    SW_MINIMIZE = 6
    SW_RESTORE = 9
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    def _to_absolute(x: int, y: int) -> tuple[int, int]:
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1
        ax = int(round((x - vx) * 65535 / (vw - 1))) if vw > 1 else 0
        ay = int(round((y - vy) * 65535 / (vh - 1))) if vh > 1 else 0
        return ax, ay

    def _alt_blip() -> None:
        """Press+release Alt via SendInput. Resets our process's
        foreground-lock timer so the next SetForegroundWindow succeeds."""
        arr = (INPUT * 2)()
        arr[0].type = INPUT_KEYBOARD
        arr[0].ki = KEYBDINPUT(VK_MENU, 0, 0, 0, 0)
        arr[1].type = INPUT_KEYBOARD
        arr[1].ki = KEYBDINPUT(VK_MENU, 0, KEYEVENTF_KEYUP, 0, 0)
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))

    def _click_at(x: int, y: int) -> None:
        ax, ay = _to_absolute(x, y)
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        arr = (INPUT * 3)()
        arr[0].type = INPUT_MOUSE
        arr[0].mi = MOUSEINPUT(ax, ay, 0, move_flags, 0, 0)
        arr[1].type = INPUT_MOUSE
        arr[1].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
        arr[2].type = INPUT_MOUSE
        arr[2].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
        sent = user32.SendInput(3, arr, ctypes.sizeof(INPUT))
        if sent != 3:
            err = ctypes.get_last_error()
            log.warning("SendInput dispatched %d/3 events (last_error=%d)",
                        sent, err)

    def _activate(x: int, y: int) -> bool:
        hwnd = user32.WindowFromPoint(POINT(x, y))
        if not hwnd:
            log.warning("click activate: no window at (%d,%d)", x, y)
            return False
        top = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        if not user32.IsWindow(top):
            return False
        fg_before = user32.GetForegroundWindow()
        if fg_before == top:
            return True

        if user32.IsIconic(top):
            user32.ShowWindow(top, SW_SHOW)

        target_tid = user32.GetWindowThreadProcessId(top, None)
        our_tid = kernel32.GetCurrentThreadId()

        # Reset foreground-lock timer so SetForegroundWindow isn't vetoed.
        _alt_blip()

        attached = False
        if target_tid and target_tid != our_tid:
            attached = bool(user32.AttachThreadInput(our_tid, target_tid, True))
        try:
            user32.BringWindowToTop(top)
            sfw_result = bool(user32.SetForegroundWindow(top))
        finally:
            if attached:
                user32.AttachThreadInput(our_tid, target_tid, False)

        fg_after = user32.GetForegroundWindow()
        ok = fg_after == top
        if not ok:
            # Fallback: minimize + restore. This is a documented Windows
            # trick that bypasses the foreground-lock restriction — the
            # restore call always takes foreground, no veto. Yes it
            # flickers. It works.
            user32.ShowWindow(top, SW_MINIMIZE)
            time.sleep(0.02)
            user32.ShowWindow(top, SW_RESTORE)
            time.sleep(0.08)
            fg_after = user32.GetForegroundWindow()
            ok = fg_after == top
            if not ok:
                log.warning(
                    "click activate: Chrome STILL not foreground after minimize/restore "
                    "(target=0x%x fg=0x%x)",
                    top, fg_after,
                )
        return ok

    _sendinput_click = _click_at
    _bring_to_front = _activate
    _WIN32_READY = True


class RecordingClickDriver:
    """Test double: records every click instead of executing it."""

    def __init__(self) -> None:
        self.calls: list[Point] = []

    def click_point(self, point: Point) -> None:
        self.calls.append(point)
