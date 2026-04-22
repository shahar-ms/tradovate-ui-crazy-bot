"""
Standalone click helper. Invoked as a short-lived subprocess so we get a
fresh Windows process with no accumulated Qt/hook state.

Usage:
    python -m app.execution._click_helper <x> <y>

This deliberately mirrors click_test.py (which the user confirmed works
end-to-end). If the bot's in-process SendInput is being blackholed by
something in our Qt host, this subprocess path is immune to it.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes


SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SPIF_SENDCHANGE = 0x02


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _kill_foreground_lock(user32) -> None:
    user32.SystemParametersInfoW.argtypes = (
        wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
    )
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    user32.SystemParametersInfoW(
        SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE,
    )


def _build_api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _UN(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("_u",)
        _fields_ = [("type", wintypes.DWORD), ("_u", _UN)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.WindowFromPoint.argtypes = (POINT,); user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT); user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,); user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,); user32.BringWindowToTop.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    return user32, kernel32, INPUT, MOUSEINPUT, KEYBDINPUT, POINT


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12
GA_ROOT = 2


def _to_absolute(user32, x: int, y: int) -> tuple[int, int]:
    vx = user32.GetSystemMetrics(76); vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78) or 1; vh = user32.GetSystemMetrics(79) or 1
    ax = int(round((x - vx) * 65535 / (vw - 1))) if vw > 1 else 0
    ay = int(round((y - vy) * 65535 / (vh - 1))) if vh > 1 else 0
    return ax, ay


def _alt_blip(user32, INPUT, KEYBDINPUT) -> None:
    arr = (INPUT * 2)()
    arr[0].type = INPUT_KEYBOARD
    arr[0].ki = KEYBDINPUT(VK_MENU, 0, 0, 0, 0)
    arr[1].type = INPUT_KEYBOARD
    arr[1].ki = KEYBDINPUT(VK_MENU, 0, KEYEVENTF_KEYUP, 0, 0)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def _activate(user32, kernel32, POINT, x: int, y: int) -> bool:
    hwnd = user32.WindowFromPoint(POINT(x, y))
    if not hwnd:
        return False
    top = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    if user32.GetForegroundWindow() == top:
        return True
    target_tid = user32.GetWindowThreadProcessId(top, None)
    our_tid = kernel32.GetCurrentThreadId()
    attached = False
    if target_tid and target_tid != our_tid:
        attached = bool(user32.AttachThreadInput(our_tid, target_tid, True))
    try:
        user32.BringWindowToTop(top)
        user32.SetForegroundWindow(top)
    finally:
        if attached:
            user32.AttachThreadInput(our_tid, target_tid, False)
    return user32.GetForegroundWindow() == top


def _click_at(user32, INPUT, MOUSEINPUT, x: int, y: int) -> None:
    ax, ay = _to_absolute(user32, x, y)
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    arr = (INPUT * 3)()
    arr[0].type = INPUT_MOUSE
    arr[0].mi = MOUSEINPUT(ax, ay, 0, flags, 0, 0)
    arr[1].type = INPUT_MOUSE
    arr[1].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
    arr[2].type = INPUT_MOUSE
    arr[2].mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
    user32.SendInput(3, arr, ctypes.sizeof(INPUT))


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("click_helper only runs on win32", file=sys.stderr)
        return 2
    if len(argv) < 3:
        print("usage: _click_helper <x> <y>", file=sys.stderr)
        return 2
    x, y = int(argv[1]), int(argv[2])

    _set_dpi_aware()

    user32, kernel32, INPUT, MOUSEINPUT, KEYBDINPUT, POINT = _build_api()

    _kill_foreground_lock(user32)
    _alt_blip(user32, INPUT, KEYBDINPUT)
    _activate(user32, kernel32, POINT, x, y)
    time.sleep(0.08)
    _click_at(user32, INPUT, MOUSEINPUT, x, y)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
