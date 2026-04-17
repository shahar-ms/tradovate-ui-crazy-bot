# Part 1 — Tradovate UI Price Stream via User-Marked Screen Regions

## Objective
Build a **local real-time price stream** from the Tradovate web application **without using Tradovate API access**.

This component must let the user manually mark the exact screen region where the live price is shown on their specific Tradovate layout. The system then reads that region continuously and emits normalized price ticks for the signal engine.

This spec is written for a coding LLM and is intentionally implementation-oriented.

---

## Chosen stack
Use **Python** for this part.

### Why Python is the best fit here
The user explicitly confirmed these conditions:
- the user can manually mark the relevant elements on screen,
- the screen layout will remain fixed,
- the PC will not sleep/lock during trading,
- execution is desktop/UI based rather than API based.

Given those constraints, Python is a better fit than TypeScript because it can handle all of the following in one runtime cleanly:
- fast screen capture,
- region cropping,
- OCR,
- image preprocessing,
- coordinate-based automation,
- local config/calibration UI,
- desktop process supervision.

### Recommended Python libraries
- `mss` — fast screen capture
- `opencv-python` — preprocessing and optional template matching
- `numpy` — image arrays
- `pytesseract` — OCR fallback / OCR primary for price text
- `Pillow` — image helpers
- `pydantic` — config validation
- `pyautogui` or `pynput` — optional mouse utilities for calibration
- `tkinter` or `customtkinter` — simple calibration overlay UI
- `orjson` or standard `json` — config persistence

---

## Core design choice
Do **not** depend on Tradovate DOM selectors for v1.

Instead, use this priority:

1. **User-marked screen region** for price read
2. OCR-based numeric parsing from that region
3. Confidence scoring + sanity checks
4. Emit normalized price ticks only when valid
5. Fail closed if price cannot be trusted

This is the right design because the user has already accepted a fixed-layout workflow.

---

## What the user must mark during calibration
The calibration tool must allow the user to mark these items on the current Tradovate screen:

1. `price_region`
   - a rectangle covering only the changing current price text
   - should include enough padding to absorb tiny UI shifts
   - should exclude unrelated numbers whenever possible

2. `instrument_anchor_region` (recommended)
   - a rectangle containing visible `MNQ` or contract text
   - used as a safety check that the correct instrument is on screen

3. `status_region` (optional but recommended)
   - small area where execution confirmations/errors may appear
   - useful for downstream execution acknowledgements

The user may also mark execution buttons in Part 3, but this file is focused on price capture.

---

## Assumptions
1. OS: Windows 10/11
2. Browser: Tradovate web app open in Chrome/Chromium/Edge
3. One dedicated monitor or a stable monitor arrangement
4. Fixed browser size and position during runtime
5. Windows display scaling remains unchanged during session
6. Tradovate theme/font/zoom remain unchanged during session
7. Only one instrument is actively monitored for v1
8. Instrument is MNQ only for v1
9. User keeps the marked price area visible at all times
10. No overlapping windows cover the marked regions

---

## Architecture

```text
tradovate_bot/
  price_stream/
    app.py
    config.py
    models.py
    capture.py
    preprocess.py
    ocr_reader.py
    parser.py
    validator.py
    heartbeat.py
    calibrator.py
    stream_bus.py
    logs.py
    screenshots/
    config/
      screen_map.json
      bot_config.json
```

---

## Data contract

```python
from dataclasses import dataclass
from typing import Optional, Literal

@dataclass
class PriceTick:
    ts_local: str
    root_symbol: str               # "MNQ"
    contract_text: Optional[str]   # optional OCR from anchor region
    price: float
    raw_text: str
    confidence: float              # 0.0..1.0
    source_mode: Literal["ocr", "ocr+rules", "template_match"]
    sequence: int
    frame_latency_ms: int
```

### Stream health

```python
@dataclass
class StreamHealth:
    status: str                    # healthy/degraded/stalled/broken
    last_tick_ts: Optional[str]
    last_good_price: Optional[float]
    consecutive_failures: int
    consecutive_no_change_frames: int
    fps: float
    reason: Optional[str]
```

---

## Screen map config
Persist the user-marked regions in JSON.

```json
{
  "monitor_index": 1,
  "window_title_hint": "Tradovate",
  "regions": {
    "price_region": { "x": 1442, "y": 164, "w": 124, "h": 42 },
    "instrument_anchor_region": { "x": 1310, "y": 164, "w": 110, "h": 36 },
    "status_region": { "x": 1260, "y": 980, "w": 300, "h": 42 }
  },
  "calibration_meta": {
    "display_scale": 100,
    "browser_zoom": 100,
    "screen_resolution": [2560, 1440],
    "theme": "dark",
    "captured_at": "2026-04-17T21:00:00+03:00"
  }
}
```

---

## Price reading pipeline

### Step 1 — Capture
Capture only the `price_region`, not the whole screen.

Requirements:
- capture interval target: **30–60 FPS** if possible
- if OCR becomes CPU-heavy, decouple capture FPS from parse FPS
- maintain latest-frame buffer only; do not allow lag buildup

### Step 2 — Preprocess image
For each frame crop:
1. convert to grayscale
2. enlarge 2x or 3x
3. contrast normalize
4. threshold / adaptive threshold
5. optionally invert depending on theme
6. optional denoise / morphology for sharper digits

Produce 2–3 preprocessing variants because one may OCR better than another.

### Step 3 — OCR / parse
Run OCR constrained to likely characters only:
- digits `0-9`
- decimal point `.`
- optional comma `,` if UI shows thousand separators

Then normalize:
- remove spaces
- remove commas
- reject multiple decimal points
- parse float

### Step 4 — Sanity rules
Only accept parsed values if all conditions pass:
- numeric parse succeeded
- price aligns to MNQ tick size: `price % 0.25 == 0` within epsilon
- price jump vs prior accepted price is not absurd
- instrument anchor still looks like MNQ if anchor region enabled

### Step 5 — Emit tick
Emit a new tick only when:
- price changed from last accepted price, or
- heartbeat mode requires a periodic duplicate tick snapshot

---

## OCR strategy details
Use a two-stage OCR strategy.

### Stage A — fast OCR
Attempt small-region OCR with constrained characters.

### Stage B — corrective normalization
Apply custom cleanup rules before parse:
- `O` -> `0`
- `I` / `l` -> `1`
- stray `:` -> `.`
- strip leading/trailing garbage

### Stage C — confidence scoring
Confidence score should combine:
- OCR engine confidence if available
- whether parse succeeded cleanly
- tick-size validity
- similarity to expected price neighborhood
- whether repeated frames agree

Example score logic:

```python
def compute_confidence(raw_text, parsed_price, prev_price, tick_ok, anchor_ok):
    score = 0.0
    if raw_text:
        score += 0.25
    if parsed_price is not None:
        score += 0.35
    if tick_ok:
        score += 0.20
    if anchor_ok:
        score += 0.10
    if prev_price is None or abs(parsed_price - prev_price) <= 10:
        score += 0.10
    return min(score, 1.0)
```

---

## MNQ normalization rules
For MNQ, minimum tick size is **0.25 points**. That means all accepted prices must resolve to quarter-point increments. citeturn245392search2

Use:

```python
TICK_SIZE = 0.25
EPS = 1e-6

def is_valid_tick_price(price: float) -> bool:
    ticks = round(price / TICK_SIZE)
    return abs(price - ticks * TICK_SIZE) < EPS
```

---

## Heartbeat and stall detection
The stream must fail closed if price stops updating or OCR quality collapses.

### Health states
- `healthy` — fresh valid ticks arriving
- `degraded` — some OCR failures, but recent valid ticks still exist
- `stalled` — no valid update for too long
- `broken` — repeated parse failures or anchor mismatch

### Suggested thresholds
- `warn_no_tick_ms = 1200`
- `stalled_no_tick_ms = 3000`
- `broken_failures = 8`
- `max_jump_points = 40` unless explicitly allowed

If `stalled` or `broken`, downstream entry logic must stop opening trades.

---

## Calibration tool requirements
Build a small calibration desktop app.

### Must support
1. choose monitor
2. freeze a screenshot of the current screen
3. draw rectangles with mouse
4. label each region (`price_region`, `instrument_anchor_region`, `status_region`)
5. preview cropped image live
6. run test OCR live on the chosen `price_region`
7. save config JSON
8. load existing config and edit it

### Nice to have
- nudge region by arrow keys
- resize by keyboard
- store 3 OCR presets for the same region
- save sample crops for debugging

---

## Runtime process flow

```text
load config
 -> verify screen resolution/scaling match calibration
 -> capture instrument anchor region
 -> verify expected instrument looks like MNQ
 -> start high-frequency capture loop for price_region
 -> preprocess crop
 -> OCR parse
 -> validate tick increment and jump bounds
 -> emit PriceTick
 -> update StreamHealth
 -> write debug snapshot on repeated failures
```

---

## Recommended classes

```python
class ScreenMap:
    monitor_index: int
    price_region: dict
    instrument_anchor_region: dict | None
    status_region: dict | None

class ScreenCapturer:
    def capture_region(self, region: dict) -> "np.ndarray": ...

class PriceOCR:
    def read_price(self, image) -> tuple[str, float | None, float]: ...

class PriceValidator:
    def validate(self, price, prev_price, root_symbol) -> tuple[bool, str | None]: ...

class PriceStreamService:
    def run(self) -> None: ...
```

---

## Minimal config for bot runtime

```json
{
  "symbol": "MNQ",
  "tick_size": 0.25,
  "capture": {
    "target_fps": 40,
    "parse_every_nth_frame": 1,
    "warn_no_tick_ms": 1200,
    "stalled_no_tick_ms": 3000
  },
  "ocr": {
    "engine": "tesseract",
    "psm": 7,
    "whitelist": "0123456789.,",
    "scale_factor": 3,
    "use_adaptive_threshold": true,
    "invert": false,
    "min_confidence": 0.82
  },
  "validation": {
    "max_jump_points": 40,
    "require_anchor_match": true,
    "anchor_expected_text": "MNQ"
  },
  "debug": {
    "save_failed_crops": true,
    "save_every_nth_success": 0
  }
}
```

---

## Failure handling
If any of the following happen, mark stream unhealthy and block trading:
- price OCR repeatedly unreadable
- instrument anchor no longer matches expected contract/root
- marked region appears covered or blank
- screen resolution changed from calibration
- user moved browser / changed layout enough that OCR quality collapses

### Required debug artifacts on failure
Save:
- raw crop image
- preprocessed crop image
- OCR raw string
- parsed float or `None`
- previous accepted price
- health state transition reason

---

## Testing plan

### Unit tests
- string normalization
- float parsing
- tick-size validation
- jump filtering
- confidence scoring

### Offline tests
Use stored screenshots/crops to verify OCR robustness on:
- dark mode
- light mode
- different price lengths
- fast-moving price changes

### Live dry run
Run for 30–60 minutes in simulation mode and log:
- parse success rate
- valid tick rate
- mean time between failures
- average OCR latency
- mismatch count vs visual chart observation

Do not connect to real execution until the stream is visibly trustworthy.

---

## Pseudocode

```python
prev_price = None
sequence = 0

while True:
    crop = capturer.capture_region(screen_map.price_region)
    raw_text, parsed_price, ocr_conf = price_ocr.read_price(crop)

    anchor_ok = True
    if screen_map.instrument_anchor_region:
        anchor_ok = instrument_reader.anchor_matches("MNQ")

    valid = False
    reason = None
    if parsed_price is None:
        reason = "parse_failed"
    elif not is_valid_tick_price(parsed_price):
        reason = "invalid_tick_step"
    elif prev_price is not None and abs(parsed_price - prev_price) > config.validation.max_jump_points:
        reason = "jump_too_large"
    elif not anchor_ok:
        reason = "instrument_anchor_mismatch"
    else:
        valid = True

    if valid:
        confidence = compute_confidence(raw_text, parsed_price, prev_price, True, anchor_ok)
        if confidence >= config.ocr.min_confidence and parsed_price != prev_price:
            sequence += 1
            emit_tick(
                PriceTick(
                    ts_local=now_iso(),
                    root_symbol="MNQ",
                    contract_text=None,
                    price=parsed_price,
                    raw_text=raw_text,
                    confidence=confidence,
                    source_mode="ocr+rules",
                    sequence=sequence,
                    frame_latency_ms=last_frame_ms,
                )
            )
            prev_price = parsed_price
            health.mark_good(parsed_price)
        else:
            health.mark_soft_failure("confidence_too_low")
    else:
        health.mark_failure(reason)
        debug.save_failure(crop, raw_text, parsed_price, reason)
```

---

## Implementation note for the coding LLM
This component should be designed as a **strict producer only**. It must not decide entries or clicks. Its job is to produce the cleanest possible local price stream from a user-marked Tradovate screen region.
