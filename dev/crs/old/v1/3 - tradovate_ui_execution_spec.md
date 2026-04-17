# Part 3 — Tradovate Web UI Execution via User-Marked Elements

## Objective
Build a **desktop/UI execution layer** that performs exactly these three actions on the Tradovate web application:

- `BUY`
- `SELL`
- `CANCEL_ALL`

The user explicitly wants the ability to mark all needed element locations on the specific Tradovate screen. Therefore this part must support a calibration workflow where the user records the click points or click regions for:
- price read area (shared with Part 1)
- buy button
- sell button
- cancel-all control

The system may rely on the screen remaining fixed during runtime.

---

## Chosen stack
Use **Python** here as well.

### Why Python fits best
Because this is coordinate-based desktop automation with a fixed screen, Python provides the simplest single-stack path for:
- coordinate capture,
- calibration UI,
- mouse clicks,
- keyboard fallback,
- screenshot-based verification,
- local watchdog logic.

Recommended libraries:
- `pyautogui` — click/type/move helpers
- `pynput` — low-level keyboard/mouse hooks if needed
- `mss` — quick post-action screenshot checks
- `opencv-python` — optional template matching / visual verification
- `tkinter` — calibration overlay
- `pydantic` — config validation

PyAutoGUI is designed specifically to let Python scripts control mouse and keyboard across desktop applications. citeturn245392search8

---

## Design philosophy
This component is **not** a strategy engine.
It is a thin executor that:
1. receives an intent,
2. confirms the screen still looks like the calibrated trading screen,
3. performs the click or hotkey,
4. optionally checks for visual acknowledgement,
5. reports success or failure.

---

## Important Tradovate facts relevant to execution
Tradovate’s web application supports configurable hotkeys, and its documented controls include order-management actions such as Cancel All / Exit-at-market-and-cancel behavior. That means the executor should support both direct button clicking and optional hotkey mode, even if the primary mode is coordinate-based clicking. citeturn245392search0turn245392search1turn245392search9

---

## Primary mode and fallback mode

### Primary mode: coordinate click
The user marks screen coordinates/regions during calibration. Runtime uses those exact coordinates.

### Fallback mode: hotkey execution
If the user configures unique Tradovate hotkeys, the bot may use hotkeys as a fallback or even as primary mode.

### Optional confirmation mode
Use screen-region checks to confirm the UI likely responded after click.

---

## Assumptions
1. Tradovate is already logged in
2. Target workspace is already open
3. Browser position and size do not change
4. Screen resolution and Windows display scaling do not change
5. Browser zoom does not change
6. The monitored instrument is already selected correctly
7. The quantity is already configured manually before session start
8. No modal dialogs cover the marked action points
9. The user will not move the mouse during execution-critical moments if PyAutoGUI movement is visible
10. One Tradovate window only for automation in v1

---

## Calibration requirements
The calibration tool must support marking the following execution elements:

### Mandatory elements
- `buy_point` or `buy_region`
- `sell_point` or `sell_region`
- `cancel_all_point` or `cancel_all_region`

### Recommended verification regions
- `instrument_anchor_region`
- `position_status_region`
- `orders_status_region`
- `toast_or_alert_region`

### Region vs point rule
For each action, allow either:
- a single click point, or
- a rectangular region with a preferred click offset inside the region

Use region mode when buttons may move by a few pixels.

---

## Config schema

```json
{
  "execution": {
    "mode": "click",
    "fallback_mode": "hotkey",
    "click_move_duration_ms": 40,
    "post_click_delay_ms": 180,
    "double_check_before_click": true,
    "action_timeout_ms": 2500,
    "require_foreground_window": true,
    "pause_after_cancel_all_ms": 300
  },
  "screen": {
    "monitor_index": 1,
    "expected_resolution": [2560, 1440],
    "expected_scale_percent": 100,
    "window_title_hint": "Tradovate"
  },
  "points": {
    "buy_point": { "x": 2380, "y": 278 },
    "sell_point": { "x": 2380, "y": 336 },
    "cancel_all_point": { "x": 2380, "y": 420 }
  },
  "regions": {
    "instrument_anchor_region": { "x": 1310, "y": 164, "w": 110, "h": 36 },
    "position_status_region": { "x": 2150, "y": 160, "w": 180, "h": 52 },
    "orders_status_region": { "x": 2010, "y": 890, "w": 420, "h": 120 },
    "toast_or_alert_region": { "x": 1780, "y": 120, "w": 500, "h": 80 }
  },
  "hotkeys": {
    "buy": "alt+b",
    "sell": "alt+s",
    "cancel_all": "alt+c"
  },
  "safety": {
    "block_if_anchor_missing": true,
    "block_if_screen_signature_changed": true,
    "single_flight_actions": true,
    "max_failed_actions": 1,
    "halt_on_ack_failure": true
  }
}
```

---

## Screen signature check
Before each action, the executor must verify that the screen still matches the calibrated environment.

### Minimum required checks
1. monitor resolution matches calibration
2. anchor region still visually resembles the saved reference
3. optional OCR in anchor region still contains `MNQ`
4. browser/window is foreground if required
5. no unrecoverable modal/overlay detected in important action regions

### Implementation suggestion
At calibration time, store small reference images for:
- buy region background
- sell region background
- cancel-all region background
- instrument anchor region

At runtime, compare a fresh crop against reference using one of:
- simple pixel diff tolerance,
- normalized correlation,
- template matching,
- perceptual hash.

If similarity is too low, block action and halt.

---

## Input / output contracts

### Input intent

```python
@dataclass
class ExecutionIntent:
    intent_id: str
    ts_local: str
    action: str                     # BUY / SELL / CANCEL_ALL
    reason: str
    qty: int
    expected_price: float | None
    position_effect: str            # open / close / flat_cleanup
    metadata: dict
```

### Output ack

```python
@dataclass
class ExecutionAck:
    intent_id: str
    ok: bool
    action: str
    ts_local: str
    method_used: str                # click / hotkey
    message: str | None
    confidence: float
```

---

## Action flow

### Shared pre-action checks
Before every action:
1. verify no other action is in flight
2. verify calibrated screen signature still valid
3. verify instrument anchor still indicates MNQ if enabled
4. ensure target point/region is on current monitor bounds
5. bring Tradovate window to foreground if required
6. optional micro-pause to stabilize

If any check fails, reject action and halt.

---

## BUY implementation

### Click mode
1. run pre-action checks
2. move mouse to `buy_point`
3. optional tiny hover pause `20–50ms`
4. left click once
5. wait `post_click_delay_ms`
6. run acknowledgement detector
7. publish success/failure ack

### Hotkey mode
1. run pre-action checks
2. focus Tradovate window
3. send configured buy hotkey
4. wait `post_click_delay_ms`
5. run acknowledgement detector
6. publish ack

---

## SELL implementation
Mirror BUY, using `sell_point` or hotkey.

---

## CANCEL_ALL implementation

### Click mode
1. run pre-action checks
2. move to `cancel_all_point`
3. click once
4. wait `pause_after_cancel_all_ms`
5. run acknowledgement detector
6. publish ack

### Hotkey mode
1. run pre-action checks
2. focus Tradovate window
3. send configured cancel-all hotkey
4. wait `pause_after_cancel_all_ms`
5. run acknowledgement detector
6. publish ack

---

## Acknowledgement detection
Acks are probabilistic because the executor is not using broker API.

### Acceptable confirmation signals
Any of these may be used:
- visible toast/notification appears in `toast_or_alert_region`
- position status region changes
- working-order indicators disappear after `CANCEL_ALL`
- button visual state changes in a known way
- screen diff around the relevant region strongly indicates the click took effect

### Confidence scoring
Ack confidence can combine:
- action completed without screen mismatch
- click landed inside calibrated region
- expected region changed after click
- toast/status text matched expected action

If ack confidence is too low, return failure and halt.

---

## Preventing duplicate clicks
The executor must guarantee **single-flight action processing**.

Rules:
- do not process a second intent while one is unresolved
- deduplicate repeated `intent_id`
- add a short action debounce window
- optionally require engine-side acknowledgement before next action

---

## Calibration tool requirements
The same desktop calibration app may be shared across all 3 parts.

### Must support
1. capture current screen screenshot
2. click to mark action points
3. optionally drag rectangles for regions
4. save named points and regions
5. load and edit existing maps
6. test-click mode with safety countdown
7. save reference crops for later comparison
8. display current monitor coordinates live

### Recommended test workflow
- user marks buy, sell, cancel-all
- tool shows a 3-second countdown
- tool test-clicks one marked point in a safe sim environment
- user confirms the click landed correctly

---

## Safety rules
These are mandatory.

### Rule 1 — Simulation-first
New calibration should first be tested on Tradovate simulation mode.

### Rule 2 — Foreground lock
If configured, do not click unless Tradovate is foreground.

### Rule 3 — Screen drift halt
If anchor similarity drops below threshold, halt.

### Rule 4 — One instrument only
If anchor OCR no longer indicates MNQ, halt.

### Rule 5 — No blind retries
If one click fails acknowledgement, do not spam-click repeatedly.

### Rule 6 — Emergency halt
Support a local keyboard emergency stop such as `Ctrl+Alt+Pause` that stops all new actions instantly.

---

## Suggested classes

```python
class ScreenVerifier:
    def verify_environment(self) -> tuple[bool, str | None]: ...

class ClickExecutor:
    def click_point(self, x: int, y: int) -> None: ...

class HotkeyExecutor:
    def send_combo(self, combo: str) -> None: ...

class AckDetector:
    def detect(self, action: str) -> tuple[bool, float, str | None]: ...

class ExecutionService:
    def execute(self, intent: ExecutionIntent) -> ExecutionAck: ...
```

---

## Pseudocode

```python
def execute(intent: ExecutionIntent) -> ExecutionAck:
    ok, reason = screen_verifier.verify_environment()
    if not ok:
        return ack_fail(intent, f"env_check_failed:{reason}")

    if action_lock.in_flight:
        return ack_fail(intent, "action_in_flight")

    action_lock.acquire(intent.intent_id)
    try:
        if config.execution.mode == "click":
            point = get_point_for_action(intent.action)
            pyautogui.moveTo(point["x"], point["y"], duration=config.execution.click_move_duration_ms / 1000)
            pyautogui.click()
            sleep_ms(config.execution.post_click_delay_ms)
        else:
            send_hotkey(config.hotkeys[intent.action.lower()])
            sleep_ms(config.execution.post_click_delay_ms)

        ok, conf, msg = ack_detector.detect(intent.action)
        return ExecutionAck(
            intent_id=intent.intent_id,
            ok=ok,
            action=intent.action,
            ts_local=now_iso(),
            method_used=config.execution.mode,
            message=msg,
            confidence=conf,
        )
    finally:
        action_lock.release()
```

---

## Implementation order
1. point/region calibration tool
2. screen signature verification
3. click execution service
4. optional hotkey service
5. acknowledgement detector
6. emergency stop and action lock
7. structured logs and screenshot capture on failure

---

## Final note for the coding LLM
This part should remain intentionally narrow. It should not infer trade logic or account state beyond minimal visual confirmation. It exists to reliably perform only the 3 allowed Tradovate actions on a fixed, user-calibrated screen.
