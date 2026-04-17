# 03 — Execution Layer (Buy / Sell / Cancel-All)

## Why this file exists
This is the **third file to implement**. It is responsible for performing only three actions on the Tradovate web app:
- buy
- sell
- cancel-all

This file must be built as an isolated execution adapter. It should not know the strategy details. It should receive an intent like `BUY` and turn it into one safe UI action.

---

## What this file assumes already exists
This file assumes:
- file 01 exists and produced `screen_map.json`
- file 02 exists and proved the screen and anchor concept is stable
- Tradovate layout is fixed
- user has already marked `buy_point`, `sell_point`, and `cancel_all_point`

This file does **not** depend on strategy logic yet.

---

## Technical context
PyAutoGUI is a good fit here because it provides simple mouse movement, clicking, and keyboard control from Python. It also supports screenshot/image-location helpers if a fallback is needed later. citeturn324338search5turn324338search7turn324338search9turn324338search13

Tradovate’s web platform documents configurable hotkeys and documents SuperDOM/close-related controls that can flatten positions and cancel working orders for the loaded instrument. That means the execution layer can safely support two modes:
- **primary:** click calibrated screen points
- **optional fallback:** send configured hotkeys if the user chooses that layout later citeturn572300search3turn572300search4turn572300search8

Tradovate also documents simulated account workflows, which makes sim-mode testing the correct place to prove this file before any live usage. citeturn324338search16turn324338search17

---

## Goal of this file
Build a component that accepts an execution intent:

```python
ExecutionIntent(action="BUY", reason="sweep_reversal_entry")
```

and then:
1. validates the screen is still correct
2. performs the single UI action
3. records what happened
4. returns an acknowledgement object

This file should be able to run in:
- dry-run mode
- simulated Tradovate mode
- armed/live mode

---

## Recommended internal modules
Create these files inside `app/execution/`:

```text
app/execution/
  models.py
  guards.py
  click_driver.py
  hotkey_driver.py
  ack_reader.py
  executor.py
  overlay.py
```

---

## Core models to create

### ExecutionIntent
Fields:
- `intent_id`
- `action` (`BUY`, `SELL`, `CANCEL_ALL`)
- `ts_ms`
- `reason`
- `expected_side` optional
- `metadata` optional

### ExecutionAck
Fields:
- `intent_id`
- `action`
- `status` (`ok`, `failed`, `unknown`, `blocked`)
- `ts_ms`
- `message`
- `screen_guard_passed`
- `evidence_path` optional

### ExecutionConfig
Fields:
- `move_duration_ms`
- `pre_click_delay_ms`
- `post_click_delay_ms`
- `double_click_enabled`
- `enable_hotkey_fallback`
- `hotkeys`
- `max_unknown_acks_before_halt`
- `anchor_match_threshold`
- `dry_run`

---

## Execution philosophy
This file should never “think like a trader.” It should only act like a safe remote hand.

Core rule:
- **one intent in, one action out**

No hidden retries for entries.
No extra clicks.
No strategy decisions.

---

## Implementation order inside this file
Build in this order:

1. screen guard
2. low-level click driver
3. optional hotkey driver
4. visual overlay test utility
5. executor wrapper
6. acknowledgement logic
7. dry-run mode
8. sim-mode validation
9. armed-mode safety gates

---

## 1. Screen guard
Implement `guards.py` first.

The screen guard must verify, before any real action:
- monitor index still exists
- current screen size matches calibrated size
- current anchor crop still matches saved anchor reference
- the click point is inside current screen bounds

If any check fails:
- block the action
- return `ExecutionAck(status="blocked")`
- never click

This is the most important safety gate in this file.

---

## 2. Click driver
Implement `click_driver.py`.

Responsibilities:
- move mouse to a point
- optional small settle delay
- click once
- optional screenshot before/after

Use PyAutoGUI’s `moveTo()` and `click()`. Keep all timings configurable. citeturn324338search9

Suggested interface:

```python
class ClickDriver:
    def click_point(self, point: Point) -> None: ...
```

Do not add strategy or validation here.

---

## 3. Optional hotkey driver
Implement `hotkey_driver.py`.

This is optional fallback only.

Why keep it:
- Tradovate documents configurable web hotkeys. If later the user prefers keyboard execution, the same executor can switch modes without redesign. citeturn572300search4turn572300search8

Interface:

```python
class HotkeyDriver:
    def send(self, combo: str) -> None: ...
```

Use PyAutoGUI keyboard functions for key presses. citeturn324338search13

Default v1 mode should still be **point-click execution**.

---

## 4. Visual overlay tool
Implement `overlay.py`.

This is a very practical manual testing tool.

It should:
- capture current monitor
- draw circles on buy/sell/cancel points
- draw rectangles around anchor/price/status regions
- save preview image

Purpose:
- quickly confirm calibration still matches reality before live tests

---

## 5. Acknowledgement design
Implement `ack_reader.py` carefully.

Acknowledgement in screen automation is imperfect, so make it explicit.

Possible ack sources:
- optional `status_region` OCR or image check
- optional `position_region` OCR or image change check
- simple elapsed-time ack when there is no reliable visual confirmation

Statuses:
- `ok` — some evidence suggests action happened
- `failed` — direct evidence says it did not happen
- `unknown` — cannot confirm either way
- `blocked` — guard prevented action

Important rule:
- after `BUY` or `SELL`, if ack is `unknown`, the higher-level bot should halt instead of assuming success

Do not hide uncertainty.

---

## 6. Executor wrapper
Implement `executor.py`.

Responsibilities:
- receive `ExecutionIntent`
- run screen guard
- choose click or hotkey mode
- perform action
- get ack
- log everything
- return `ExecutionAck`

Suggested skeleton:

```python
class Executor:
    def execute(self, intent: ExecutionIntent) -> ExecutionAck:
        if not self.guard.check():
            return blocked_ack

        if self.config.dry_run:
            return simulated_ack

        self.driver.perform(intent)
        return self.ack_reader.read(intent)
```

---

## Dry-run behavior
Dry-run mode must:
- never click
- print exactly what would happen
- save overlay screenshots
- return `status="ok"` only as a simulated/dry-run result

This is how the coder should test the plumbing first.

---

## Testing sequence for this file
Follow this order exactly:

### Stage A — overlay only
- draw all points/regions
- confirm on saved image they are correct

### Stage B — dry-run intent test
- send fake `BUY`, `SELL`, `CANCEL_ALL`
- confirm logs show intended action
- no clicks should happen

### Stage C — live cancel-all in sim
- test safest action first
- confirm guard passes
- click marked cancel-all point
- inspect Tradovate visually

### Stage D — live buy/sell in sim
- one click at a time
- human watching the screen
- stop immediately on misalignment

Only after all four stages pass should this file be considered complete.

---

## Safety rules this file must enforce

1. **single-flight execution**
   - only one action can run at once

2. **guard-first execution**
   - every action must pass the screen guard

3. **no hidden retries for entry**
   - if buy/sell ack is unknown, return unknown and let orchestrator halt

4. **bounded click points**
   - never click outside the calibrated screen

5. **no self-repair guesses**
   - if calibration drifts, stop instead of trying random offsets

---

## What not to build in file 03
Do not build:
- signal generation
- take profit / stop loss logic
- stateful trade management
- tick parsing
- orchestrator queues

This file is only about **turning intents into safe UI actions**.

---

## Acceptance criteria
File 03 is complete only when:
- buy, sell, and cancel-all can be triggered by intent
- screen guard blocks actions on anchor mismatch
- dry-run mode works
- sim-mode click tests work with human supervision
- the component returns explicit ack states instead of guessing

---

## Handoff to file 04
File 04 will produce intents like:
- `CANCEL_ALL`
- `BUY`
- `SELL`

It should not care how the click happens. It should only call this execution adapter.

---

## Final instruction to the coding LLM
Your task here is not to “trade.” Your task is to build a **safe, boring execution hand** that can click three known spots reliably and refuse to act when the screen is not trustworthy.
