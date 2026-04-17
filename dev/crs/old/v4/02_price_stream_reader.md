# 02 — Price Stream Reader

## Why this file exists
This is the **second file to implement**. It turns the calibrated price rectangle into a clean stream of validated MNQ price ticks.

This file should be built as if it were its own product:
- read screen
- preprocess image
- OCR the price
- parse and validate it
- publish a clean `PriceTick`
- never click anything

If the price reader is unstable, every strategy built on top of it will be garbage.

---

## What this file assumes already exists
This file assumes file 01 is already done and provides:
- `screen_map.json`
- `bot_config.json`
- a correct `price_region`
- a selected monitor
- a valid anchor region
- logging base

If those do not exist, stop and implement file 01 first.

---

## Technical context
`mss` is a good fit for the capture layer because it is optimized for screen grabbing, has no heavy dependencies, and integrates well with NumPy/OpenCV. citeturn324338search2

For OCR preprocessing, OpenCV’s official docs support exactly the tools needed here:
- thresholding
- adaptive thresholding
- Otsu thresholding
- morphology operations such as open/close
- resizing with controlled interpolation citeturn123605search0turn123605search1turn123605search7turn123605search16

Tesseract’s docs explicitly note that when the expected character set is small, using `tessedit_char_whitelist` can improve recognition quality. That fits this bot well because the price region should mostly contain digits, decimal points, and maybe minus signs. citeturn572300search5

For MNQ normalization, CME’s official contract specs list the minimum price fluctuation as **0.25 index points = $0.50**. The reader should use this as a hard validator for parsed prices. citeturn419217search1

---

## Goal of this file
Build a component that repeatedly outputs objects like:

```python
PriceTick(
    ts_ms=1714681234567,
    raw_text="19234.25",
    price=19234.25,
    confidence=92.0,
    frame_id=8143,
    accepted=True,
    reject_reason=None,
)
```

This component must be usable in two modes:
1. **live mode** — capture current screen repeatedly
2. **replay/debug mode** — run OCR against saved image samples

---

## Recommended internal modules
Create these files inside `app/capture/`:

```text
app/capture/
  screen_capture.py
  preprocess.py
  ocr_reader.py
  parser.py
  validator.py
  price_stream.py
  debug_tools.py
```

---

## Core models to create

### PriceTick
Fields:
- `ts_ms`
- `frame_id`
- `raw_text`
- `price`
- `confidence`
- `accepted`
- `reject_reason`
- `source_image_path` optional

### StreamHealth
Fields:
- `last_success_ts_ms`
- `consecutive_failures`
- `consecutive_rejections`
- `stale`
- `health_state` (`ok`, `degraded`, `broken`)

### OCRResult
Fields:
- `raw_text`
- `confidence`
- `boxes` optional
- `engine_name`

---

## Implementation order inside this file
Build in this exact order:

1. raw screen capture
2. region crop
3. debug image save
4. preprocessing pipeline
5. OCR wrapper
6. text parsing
7. price validation
8. stream health tracking
9. price stream loop
10. replay tests

---

## 1. Screen capture module
Implement `screen_capture.py`.

Responsibilities:
- read `monitor_index` from config
- capture full monitor via `mss`
- crop `price_region`
- return image as NumPy array
- optionally save raw crops every N frames for debugging

Do not mix OCR code here.

Interface example:

```python
class ScreenCapture:
    def grab_monitor(self) -> np.ndarray: ...
    def grab_region(self, region: Region) -> np.ndarray: ...
```

---

## 2. Preprocessing module
Implement `preprocess.py`.

The price reader should support **multiple preprocessing recipes** because different price fonts or themes may need different handling.

At minimum include these recipes:
- `gray_only`
- `binary_threshold`
- `adaptive_threshold`
- `otsu_threshold`
- `scaled_2x_otsu`
- `scaled_3x_binary_close`

Recommended steps:
- convert to grayscale
- resize 2x or 3x
- optional Gaussian blur or median blur
- threshold
- optional morphology close/open

Why:
- OpenCV’s thresholding and morphological transforms are the standard tools for cleaning OCR input. citeturn123605search0turn123605search1
- OpenCV’s resize behavior and interpolation choices matter for OCR, especially when zooming small text. citeturn123605search2turn123605search7

Output should be a dictionary like:

```python
{
  "gray_only": img1,
  "otsu": img2,
  "scaled_3x_binary_close": img3,
}
```

---

## 3. OCR wrapper
Implement `ocr_reader.py`.

Use a pluggable design even if v1 only uses Tesseract.

```python
class OCRReader(Protocol):
    def read(self, image: np.ndarray) -> OCRResult: ...
```

### v1 backend: pytesseract
Requirements:
- use single-line or single-word page segmentation mode
- whitelist only expected characters: `0123456789.-`
- collect confidence when possible
- return raw text without extra parsing logic

Keep parsing out of the OCR module.

---

## 4. Parser
Implement `parser.py`.

Responsibilities:
- strip whitespace
- strip commas if they appear
- normalize multiple dots or junk characters if safely possible
- parse float
- return failure if ambiguous

Good rules:
- if the OCR result contains letters, reject unless a strict safe normalization can remove them
- if multiple numeric candidates appear, reject
- never silently invent a price

---

## 5. Price validator
Implement `validator.py`.

This validator is what turns OCR output into trusted trading data.

Validation rules for v1:
1. OCR confidence must be above configured minimum
2. parsed value must exist
3. parsed value must align to MNQ tick size 0.25
4. jump vs previous accepted price must not exceed configured sanity threshold unless repeated by later frames
5. stale frame detection if same failed state persists too long

### MNQ tick alignment
Because CME lists MNQ’s minimum fluctuation as 0.25, validate like this:

```python
is_valid_tick = abs((price * 100) % 25) < epsilon
```

Prefer a safer integer approach:

```python
quarter_ticks = round(price * 4)
normalized = quarter_ticks / 4.0
if abs(normalized - price) > 0.001:
    reject
```

---

## 6. Multi-recipe OCR voting
This is an important accuracy booster.

For each frame:
1. generate several preprocess variants
2. OCR each one
3. parse each result
4. keep only valid candidates
5. if multiple valid candidates agree on the same parsed price, prefer that price
6. if only one valid candidate exists, use it if confidence is high enough
7. if candidates disagree, reject frame

This approach is better than trusting a single OCR pass.

---

## 7. Stream health
Implement a simple health tracker.

Health states:
- `ok`
- `degraded`
- `broken`

Suggested transitions:
- `ok` -> `degraded` after N consecutive OCR failures or rejections
- `degraded` -> `broken` after longer sustained failure or staleness
- `broken` -> `ok` only after M consecutive accepted frames

This gives later files a clean signal for whether trading should be blocked.

---

## 8. Price stream loop
Implement `price_stream.py`.

Responsibilities:
- run capture loop at target FPS
- call preprocess recipes
- OCR and parse
- validate
- update health
- publish accepted ticks
- optionally publish rejected frames to debug log

Interface example:

```python
class PriceStream:
    def start(self): ...
    def stop(self): ...
    def get_latest_tick(self) -> PriceTick | None: ...
```

Do not add strategy logic here.

---

## 9. Replay/debug tooling
Implement `debug_tools.py`.

Required tools:
- save raw price crops
- save processed variants for selected frames
- save CSV or JSONL of OCR attempts
- replay saved images through OCR pipeline
- print acceptance statistics by preprocessing recipe

This will save huge time when OCR is noisy.

---

## Suggested settings for v1
These belong in config and should be tunable:

```json
{
  "capture_fps_target": 8,
  "ocr_backend": "tesseract",
  "min_ocr_confidence": 70,
  "price_stale_ms": 1500,
  "max_jump_points": 30.0,
  "save_debug_images": true,
  "debug_image_interval_sec": 10,
  "preprocess_recipes": [
    "gray_only",
    "otsu_threshold",
    "scaled_2x_otsu",
    "scaled_3x_binary_close"
  ]
}
```

---

## Pseudocode for one frame
```python
frame = capture.grab_region(price_region)
variants = preprocess.make_variants(frame)

candidates = []
for name, img in variants.items():
    ocr = reader.read(img)
    parsed = parser.parse(ocr.raw_text)
    verdict = validator.check(parsed, ocr.confidence, prev_price)
    if verdict.accepted:
        candidates.append((parsed, ocr.confidence, name))

best = choose_best_candidate(candidates)
if best:
    publish_tick(best)
else:
    publish_rejection()
```

---

## Unit tests to write
At minimum:

1. parser accepts `19234.25`
2. parser rejects `19a34.25`
3. validator rejects non-0.25 prices like `19234.17`
4. validator accepts `19234.25`
5. validator rejects extreme jump when configured
6. replay runner correctly processes sample images

---

## Manual test plan
Before moving to file 03:

1. open Tradovate in the final layout
2. run live price reader
3. print price every accepted frame
4. watch for 10+ minutes
5. confirm:
   - no nonsense prices
   - no broken confidence spam
   - health state mostly `ok`
   - accepted prices align to actual visible UI

The developer should also save at least 100 labeled sample crops from real trading hours for regression testing.

---

## What not to build in file 02
Do not build:
- mouse clicks
- buy/sell logic
- order state handling
- position state
- strategy logic
- orchestrator threading beyond a simple local loop

This file is only about **reading price reliably**.

---

## Acceptance criteria
File 02 is complete only when:
- it reads the current price from the marked region repeatedly
- accepted prices are aligned to 0.25 increments
- bad OCR does not leak into accepted ticks
- a health state is available to later files
- replay tools exist for debugging OCR

---

## Handoff to file 03
File 03 will not use OCR directly, but it will depend on:
- the same validated `screen_map.json`
- the same anchor-region safety idea
- the same logging/session structure

---

## Final instruction to the coding LLM
Treat this file as a **market data adapter built from pixels**. The strategy is not your problem yet. Your only job is to produce a stable, validated price stream that later code can trust.
