"""
Entry point for the operator UI.

    python -m app.ui.run_ui

The app is a single always-on-top floating panel. Calibration opens as a
modal dialog on first run or via the Setup button. No main window, no tray
icon, no navigation.
"""

from __future__ import annotations

import sys


def _enable_windows_dpi_awareness() -> None:
    """On Windows with display scaling > 100%, a DPI-unaware process gets
    virtualized coordinates. mss captures at physical pixels so the overlay
    LOOKS right, but pyautogui clicks land scaled-off-target. Declare the
    process per-monitor DPI-aware BEFORE importing pyautogui / Qt so both
    speak the same coordinate system as the screen capture.
    """
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


def _disable_windows_foreground_lock() -> None:
    """Set the system-wide ForegroundLockTimeout to 0 so SetForegroundWindow
    is never vetoed. Without this, Windows silently refuses to raise
    Chrome/Tradovate to the front when the bot dispatches a click — the
    click then goes to an inactive window and Tradovate ignores it.

    This is exactly what AutoHotkey / PowerToys / similar input tools do.
    Per-user setting; Windows reverts it on logoff or reboot."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x02

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SystemParametersInfoW.argtypes = (
            wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT,
        )
        user32.SystemParametersInfoW.restype = wintypes.BOOL

        # Set to 0 ms. pvParam holds the new timeout cast as a void*.
        ok = user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE,
        )

        # Read it back to verify the change actually took effect.
        current = wintypes.DWORD()
        user32.SystemParametersInfoW(
            SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(current), 0,
        )
        print(
            f"[bot] foreground-lock timeout set: ok={bool(ok)} "
            f"now={current.value}ms"
        )
    except Exception as e:
        print(f"[bot] foreground-lock tweak failed: {e}")


_enable_windows_dpi_awareness()
_disable_windows_foreground_lock()

from app.ui.hud_app import boot  # noqa: E402  (must follow DPI setup)


def main(argv: list[str] | None = None) -> int:
    return boot(argv)


if __name__ == "__main__":
    sys.exit(main())
