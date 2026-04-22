"""
Isolated click test. Runs OUTSIDE the bot. Answers: can Python send a
click that Tradovate actually receives?

Usage:
    python click_test.py

Then you have 5 seconds to click inside Tradovate (give it focus) and
hover near (but not on) the Buy Mkt button. The script will then
dispatch ONE click at the calibrated buy_point via the same SendInput
path the bot uses.

If Tradovate responds -> the bot itself has a wiring/timing issue.
If Tradovate does NOT respond -> something system-level is blocking
synthetic input to this Chrome window (UIPI, elevation mismatch,
browser extension, etc.).
"""
import json
import sys
import time
from pathlib import Path

# Force DPI-awareness before pyautogui loads (same as run_ui.py does).
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.execution.click_driver import PyAutoGUIClickDriver
from app.models.common import Point
from app.utils import paths

sm = json.loads(paths.screen_map_path().read_text(encoding="utf-8"))
buy = Point(x=sm["buy_point"]["x"], y=sm["buy_point"]["y"])

print(f"Buy point: {buy.x}, {buy.y}")
print("You have 5 seconds to click inside Tradovate to give it focus.")
for i in range(5, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

drv = PyAutoGUIClickDriver()
print("dispatching click NOW")
drv.click_point(buy)
print("click done. Did Tradovate react?")
