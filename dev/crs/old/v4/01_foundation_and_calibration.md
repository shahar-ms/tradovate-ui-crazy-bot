# 01 — Foundation and Calibration

## Why this file exists
This is the **first file to implement**. It creates the foundation the rest of the bot depends on:
- project structure
- shared config files
- shared data models
- calibration flow
- screen and runtime assumptions

Do **not** start from the strategy or execution logic first. If calibration and shared config are weak, every later part becomes unstable.

---

## Project context
The bot is a **Python desktop automation system** that reads price from a fixed Tradovate screen, decides what to do from that price stream, and performs only three actions on the Tradovate web app:
- buy
- sell
- cancel-all

This design is intentionally based on **screen automation**, not the Tradovate API, because Tradovate documents that API key generation requires a **live funded account with at least $1,000**, and live market data is configured separately inside the web trader. This makes a screen-based design the correct starting point for the user’s current constraints. citeturn324338search1turn324338search4

The bot assumes:
- one Windows machine
- one monitor used for trading
- Tradovate web app already open and logged in
- screen layout stays fixed while the bot runs
- no sleep, no lock, no monitor change, no browser zoom change
- the user manually marks all important screen locations once during calibration

---

## What this file must deliver
Implement these before moving to file 02:

1. project folder layout
2. shared Python models and config loaders
3. calibration tool for screen regions and click points
4. saved calibration artifacts on disk
5. a lightweight validation script that confirms the saved calibration is usable

When file 01 is done, later files must be able to load a single source of truth from disk and trust it.

---

## Recommended stack
Use **Python 3.11+**.

Recommended libraries:
- `pydantic` for config validation
- `dataclasses` or `pydantic` models for runtime objects
- `tkinter` or OpenCV window for calibration UI
- `mss` for fast screen capture
- `Pillow` for image saving/loading
- `numpy` for image arrays
- `opencv-python` for preprocessing and simple visual checks
- `pyautogui` later for clicks/keys

Why this stack:
- `mss` is built specifically for fast screenshots, supports multiple monitors, integrates cleanly with NumPy/OpenCV, and is thread-safe. citeturn324338search2
- PyAutoGUI is simple for mouse/keyboard automation and can also support optional image-based location helpers if needed later. citeturn324338search5turn324338search7turn324338search9turn324338search13

---

## Folder structure to create first
Use this structure exactly or very close to it:

```text
tradovate_bot/
  README.md
  requirements.txt
  .env.example
  app/
    __init__.py
    main.py
    models/
      __init__.py
      common.py
      config.py
    config/
      bot_config.json
      strategy_config.json
      screen_map.json
    calibration/
      __init__.py
      calibrator.py
      validator.py
    capture/
      __init__.py
    execution/
      __init__.py
    strategy/
      __init__.py
    orchestrator/
      __init__.py
    utils/
      __init__.py
      paths.py
      time_utils.py
      logging_utils.py
      image_utils.py
  runtime/
    logs/
    screenshots/
    sessions/
    state/
  tests/
    test_config.py
    test_calibration.py
```

---

## Shared concepts
These definitions should be created now, not later.

### 1. Point
A single click location on the screen.

```python
class Point(BaseModel):
    x: int
    y: int
```

### 2. Region
A rectangular screen area.

```python
class Region(BaseModel):
    left: int
    top: int
    width: int
    height: int
```

### 3. ScreenMap
This is the most important saved file. It describes where the bot should look and click.

Required fields:
- `monitor_index`
- `screen_width`
- `screen_height`
- `browser_name`
- `tradovate_anchor_region`
- `tradovate_anchor_reference_path`
- `price_region`
- `buy_point`
- `sell_point`
- `cancel_all_point`
- optional `position_region`
- optional `status_region`
- optional `pnl_region`
- optional `instrument_label_region`

### 4. BotConfig
Required runtime settings unrelated to strategy.

Suggested fields:
- `capture_fps_target`
- `ocr_backend`
- `min_ocr_confidence`
- `price_stale_ms`
- `anchor_match_threshold`
- `click_move_duration_ms`
- `click_post_delay_ms`
- `max_consecutive_failures`
- `paper_mode_default`
- `save_debug_images`
- `debug_image_interval_sec`

### 5. StrategyConfig
A separate file, even though the strategy is implemented later.

Required idea:
- keep strategy settings isolated from screen settings
- allow the user to tweak TP, SL, session windows, and risk guards without recalibrating

---

## Calibration philosophy
The bot should **not** discover the Tradovate UI by DOM inspection or browser automation. The user already said the screen layout can be assumed fixed. That means the most robust approach is:

1. capture full monitor screenshot
2. let the user mark required regions/points
3. save coordinates to disk
4. save reference images for later safety checks

This is simpler and more stable than mixing browser selectors, DOM scraping, and desktop clicks.

---

## Calibration tool requirements
Build a `calibrator.py` that supports this flow:

### Step 1 — choose monitor
- detect monitors through `mss`
- show the user a preview screenshot or dimensions
- let the user choose one monitor

### Step 2 — capture base screenshot
- grab full selected monitor
- display it in a simple UI window
- support zoom if needed

### Step 3 — mark required items in order
Prompt the user to mark these in sequence:

1. **Tradovate anchor region**
   - small stable area that should not change much
   - examples: module title area, account selector area, stable header area
   - save a cropped reference image to disk

2. **Price region**
   - rectangle that contains only the current price text
   - should avoid surrounding clutter
   - prioritize high contrast text

3. **Buy point**
   - exact click coordinate on the Buy button

4. **Sell point**
   - exact click coordinate on the Sell button

5. **Cancel-all point**
   - exact click coordinate on the Cancel All control

Optional:
6. position/status region
7. order-status toast region
8. instrument label region

### Step 4 — review mode
- show all saved points and regions overlaid on the screenshot
- allow the user to re-mark any item

### Step 5 — persist artifacts
Save:
- `app/config/screen_map.json`
- `runtime/screenshots/calibration_full.png`
- `runtime/screenshots/anchor_reference.png`
- optional crops for each region for debugging

---

## Required JSON format
The coder should keep the JSON simple and explicit.

Example `screen_map.json`:

```json
{
  "monitor_index": 1,
  "screen_width": 1920,
  "screen_height": 1080,
  "browser_name": "chrome",
  "tradovate_anchor_region": {"left": 20, "top": 20, "width": 220, "height": 60},
  "tradovate_anchor_reference_path": "runtime/screenshots/anchor_reference.png",
  "price_region": {"left": 842, "top": 168, "width": 128, "height": 44},
  "buy_point": {"x": 1450, "y": 877},
  "sell_point": {"x": 1532, "y": 877},
  "cancel_all_point": {"x": 1608, "y": 876},
  "position_region": {"left": 1360, "top": 740, "width": 310, "height": 120},
  "status_region": {"left": 1170, "top": 980, "width": 420, "height": 80}
}
```

---

## Validation tool requirements
Implement `validator.py` to prove calibration is usable.

Validation checks:
1. files exist
2. regions are within monitor bounds
3. points are within monitor bounds
4. anchor reference image exists
5. current anchor crop roughly matches reference crop
6. price region is non-empty
7. marked click points are visually drawn on a preview image

Validation output should be a simple report like:

```text
[OK] screen_map.json found
[OK] monitor 1 available
[OK] price_region within bounds
[OK] anchor image loaded
[OK] anchor similarity: 0.96
[OK] buy/sell/cancel points within bounds
READY_FOR_FILE_02 = true
```

---

## Anchor region design
The anchor region is the main safety check used before every real click. It exists to detect that the bot is still looking at the same Tradovate layout.

Good anchor region properties:
- stable across time
- visible at all times
- not a blinking value
- not PnL or price
- not dependent on the active order book changing

The validator can compare current crop to stored crop with:
- mean absolute difference
- histogram similarity
- normalized template matching

Keep it simple in v1. A grayscale absolute-difference score is enough.

---

## Config loader rules
All config loaders should:
- fail fast
- print exact field errors
- refuse to start if any required field is missing
- separate static config from runtime state

Do **not** hide config errors and continue.

---

## Logging requirements for file 01
Create basic logging now.

At minimum:
- console logger
- file logger
- session ID on startup
- saved path for logs

Python’s logging system supports queue-based handlers when slow logging work should be separated from performance-sensitive code, which will matter later once capture and execution loops run concurrently. citeturn123605search3turn123605search8turn123605search18

For file 01, a normal file logger is enough.

---

## Example calibrator flow pseudocode
```python
load_monitors()
selected_monitor = ask_user_to_choose_monitor()
full_image = capture_monitor(selected_monitor)

anchor_region = ask_user_to_draw_region(full_image, label="Tradovate anchor")
price_region = ask_user_to_draw_region(full_image, label="Price region")
buy_point = ask_user_to_click_point(full_image, label="Buy button")
sell_point = ask_user_to_click_point(full_image, label="Sell button")
cancel_all_point = ask_user_to_click_point(full_image, label="Cancel All")

save_anchor_crop(anchor_region)
save_screen_map_json(...)
save_overlay_preview(...)
run_validation()
```

---

## What not to build in file 01
Do not build these yet:
- OCR
- strategy logic
- live clicks
- threading
- queues
- replay mode
- PnL logic

This file is only about **foundation + calibration**.

---

## Acceptance criteria
File 01 is complete only when:

1. a new user can run calibration from scratch
2. all required points/regions are saved to disk
3. validator confirms calibration passes
4. a preview image shows all marked locations clearly
5. no later file needs to guess screen coordinates manually

---

## Handoff to file 02
File 02 will assume file 01 already gives it:
- `screen_map.json`
- `bot_config.json`
- usable `price_region`
- usable `monitor_index`
- validated monitor dimensions
- optional debug image folders

If those are not already working, file 02 should not be started.

---

## Final instruction to the coding LLM
Your goal in this file is to create a **boring, reliable calibration foundation**. If you are tempted to add trading logic here, stop. The only success condition is: later components can load the saved screen map and trust it.
