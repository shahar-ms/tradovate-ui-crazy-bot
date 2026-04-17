# Part 4 — Full Integration Spec for the Tradovate Screen Bot

## Objective
This document explains how to combine the first 3 parts into one working local trading bot:

1. **Price stream extraction** from a user-marked Tradovate screen region  
2. **Signal engine** that consumes the price stream and decides on `BUY`, `SELL`, or `CANCEL_ALL`  
3. **UI execution layer** that performs those actions on the Tradovate web interface using user-marked points/regions

This file is meant to be given to a coding LLM as the top-level orchestration and implementation guide.

---

## Main design decision
Build the whole system in **Python only**.

### Why one Python stack is best
The user constraints make Python the strongest choice:
- fixed screen layout
- user can calibrate all needed regions and click targets
- no Tradovate API trading path for now
- desktop control is allowed
- OCR / image preprocessing / screen capture / mouse control all belong in the same local runtime

This avoids unnecessary split architecture between browser automation and desktop automation.

---

## What the final bot must do
At runtime, the bot should:
1. load calibration and strategy config,
2. verify the screen still matches the calibration assumptions,
3. start a price-reading loop from the marked `price_region`,
4. normalize valid prices into a local tick stream,
5. feed ticks into the signal engine,
6. emit one of only 3 allowed intents:
   - `BUY`
   - `SELL`
   - `CANCEL_ALL`
7. execute the intent on the Tradovate web UI,
8. track position state locally,
9. enforce hard stop rules,
10. halt safely on bad screen state, bad OCR, or repeated execution uncertainty.

---

## Non-goals for v1
The implementation should **not** try to do these in v1:
- no API integration
- no multi-instrument support
- no Level 2 / DOM interpretation
- no volume-based strategy
- no broker-side position reconciliation via API
- no advanced order management beyond the 3 required actions
- no self-learning model
- no martingale / pyramiding / averaging down

Keep v1 deterministic and fail-closed.

---

## Top-level architecture

```text
tradovate_screen_bot/
  app.py
  bootstrap.py
  supervisor.py
  event_bus.py
  clock.py
  models.py
  logging_setup.py
  exceptions.py

  config/
    bot_config.json
    strategy_config.json
    screen_map.json
    runtime_state.json

  calibration/
    calibrator_app.py
    capture_helpers.py
    reference_store.py

  price_stream/
    capture.py
    preprocess.py
    ocr_reader.py
    parser.py
    validator.py
    stream.py
    heartbeat.py

  signal_engine/
    tick_buffer.py
    bar_builder.py
    level_detector.py
    sweep_detector.py
    risk_manager.py
    position_manager.py
    session_gate.py
    intent_engine.py

  execution/
    executor.py
    click_driver.py
    hotkey_driver.py
    screen_guard.py
    ack_detector.py
    window_focus.py

  storage/
    trade_log.py
    tick_log.py
    screenshot_log.py
    audit_log.py

  tests/
    test_parser.py
    test_tick_validation.py
    test_level_detector.py
    test_signal_rules.py
    test_executor_sim.py

  screenshots/
  logs/
```

---

## Recommended process model
Use **one OS process** with **three internal worker loops** for v1.

### Worker loops
1. **Price loop**
   - high-frequency screen capture
   - OCR + parse + validate
   - publish `PriceTick`

2. **Signal loop**
   - consume `PriceTick`
   - maintain bars and levels
   - manage state machine
   - publish `ExecutionIntent`

3. **Execution loop**
   - consume `ExecutionIntent`
   - perform click/hotkey action
   - publish `ExecutionAck`

### Why not multi-process first
For v1, single-process is simpler because:
- lower complexity
- shared state easier
- easier debugging
- easier coordination between OCR health, strategy state, and execution locks

If CPU becomes an issue later, move OCR into its own process.

---

## Core shared models

```python
from dataclasses import dataclass
from typing import Optional, Literal

@dataclass
class PriceTick:
    ts_local: str
    root_symbol: str
    price: float
    raw_text: str
    confidence: float
    sequence: int
    frame_latency_ms: int
    source_mode: str

@dataclass
class StreamHealth:
    status: Literal["healthy", "degraded", "stalled", "broken"]
    last_tick_ts: Optional[str]
    last_good_price: Optional[float]
    consecutive_failures: int
    reason: Optional[str]

@dataclass
class ExecutionIntent:
    intent_id: str
    ts_local: str
    action: Literal["BUY", "SELL", "CANCEL_ALL"]
    reason: str
    qty: int
    expected_price: Optional[float]
    position_effect: Literal["open", "close", "flat_cleanup"]
    metadata: dict

@dataclass
class ExecutionAck:
    intent_id: str
    ok: bool
    action: Literal["BUY", "SELL", "CANCEL_ALL"]
    ts_local: str
    method_used: str
    message: Optional[str]
    confidence: float

@dataclass
class PositionState:
    side: Literal["flat", "long", "short"] = "flat"
    qty: int = 0
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    opened_at: Optional[str] = None
    entry_intent_id: Optional[str] = None
```

---

## Configuration model
Use **3 separate config files** plus runtime state.

### 1. `screen_map.json`
User-calibrated coordinates and regions.

Contains:
- monitor index
- expected resolution
- price read region
- buy point or region
- sell point or region
- cancel-all point or region
- instrument anchor region
- optional verification regions
- reference screenshots / hashes

### 2. `strategy_config.json`
Trading and risk parameters.

Contains:
- strategy mode
- tick size
- contract qty
- stop loss ticks
- take profit ticks
- sweep distance thresholds
- cooldown settings
- max daily losses
- max consecutive losses
- active trading windows
- max holding time
- emergency halt rules

### 3. `bot_config.json`
System/runtime settings.

Contains:
- capture FPS target
- OCR mode and thresholds
- execution mode (`click` / `hotkey`)
- action delays
- logging paths
- screenshot capture policy
- heartbeat timings
- halt policy

### 4. `runtime_state.json`
Ephemeral persisted state for restart safety.

Contains:
- current bot status
- last sequence number
- last accepted price
- current local position state
- daily PnL estimate
- consecutive losses
- last action timestamp

---

## Event flow

```text
Price Capture Frame
  -> OCR / parse / validate
  -> PriceTick
  -> Signal Engine
  -> ExecutionIntent (optional)
  -> Execution Layer
  -> ExecutionAck
  -> Position Manager / Risk Manager
  -> Audit Log / Screenshots / Halt logic
```

### Event bus choice
For v1, use an **in-memory async queue**.

Recommended options:
- `asyncio.Queue`
- or a simple thread-safe queue if implementation remains sync-heavy

Prefer `asyncio` for cleaner loop coordination.

---

## Boot sequence
The application should follow this exact startup order.

### Step 1 — Load config
- load all JSON config files
- validate with `pydantic`
- refuse startup if mandatory regions/actions are missing

### Step 2 — Verify environment
- verify screen resolution
- verify display scale assumptions if detectable
- verify Tradovate screen anchor is visible
- optionally verify browser/window foreground path works

### Step 3 — Warm test of price region
- capture several frames from `price_region`
- run OCR on them
- confirm numeric parse succeeds repeatedly
- confirm prices align to 0.25 tick increments
- confirm enough confidence to continue

### Step 4 — Warm strategy engine
- collect enough ticks / micro-bars for warmup
- do not permit execution during warmup

### Step 5 — Arm execution
- set execution state to `READY`
- zero action lock
- enter supervised runtime loop

---

## Runtime modes
Support explicit runtime modes.

```python
runtime_mode = one_of(
    "calibration",
    "paper_shadow",
    "armed_live",
    "halted"
)
```

### Meaning
- `calibration`: user marks all regions and saves config
- `paper_shadow`: bot reads price and generates signals but does not click
- `armed_live`: bot can click buy/sell/cancel-all
- `halted`: no further action until manual reset

### Strong recommendation
Implementation should force this rollout:
1. calibration
2. paper_shadow
3. armed_live

Do not skip paper shadow during initial testing.

---

## Position handling philosophy
There is no API confirmation layer in v1, so local state must be strict and conservative.

### Local truth model
The bot maintains a **local assumed position state**.
That state changes only when:
- an entry intent was sent,
- execution ack was acceptable,
- no immediate failure condition was detected.

### Safety rule
If local position state becomes uncertain, the bot must:
1. emit `CANCEL_ALL`,
2. set state to `halted`,
3. require manual inspection.

### v1 restriction
Only one open position at a time.

---

## Price stream integration requirements
The signal engine must never consume raw OCR output directly.
It may only consume **validated `PriceTick` objects**.

### Validation gates before signal consumption
A price tick is eligible only if:
- parse succeeded
- confidence above configured threshold
- price change is plausible
- tick aligns to 0.25 increments
- stream is not marked broken

### Duplicate price policy
Duplicate prices may be ignored for signal purposes, but heartbeat status should still update.

---

## Signal engine integration requirements
The signal engine should operate as a state machine.

### Required states
- `BOOT`
- `WARMUP`
- `READY`
- `PRE_ENTRY_CLEANUP`
- `ENTRY_PENDING`
- `IN_POSITION`
- `EXIT_PENDING`
- `COOLDOWN`
- `HALTED`

### Critical interaction rules
1. The signal engine cannot emit entry intents if execution loop is busy.
2. The signal engine cannot emit new entries while local position is not flat.
3. Before entry, if configured, signal engine may emit `CANCEL_ALL` first.
4. After entry ack, the engine must immediately compute and track local stop/target.
5. If stop or target is hit according to price stream, the engine emits exit direction intent.
6. If execution ack fails on exit, bot halts.

---

## Execution layer integration requirements
Execution loop is intentionally thin.

### It must do only these things
- receive intent
- run screen guard
- click or hotkey
- optionally verify response
- return ack

### It must not do these things
- no strategy logic
- no risk logic
- no signal generation
- no autonomous retries without guardrails

### Single-flight rule
Only one action may be in flight at a time.
Use an action lock.

---

## Screen guard contract
Before any live action, the execution layer must confirm:
- current monitor and resolution match calibration
- Tradovate anchor region still resembles the reference
- required action point/region is within bounds
- no obvious overlay blocks the control
- bot is in `armed_live`

If any screen guard check fails:
- reject intent
- publish failed ack
- halt bot

---

## Recommended default risk configuration
These are implementation defaults only, not promises of profitability.

```json
{
  "root_symbol": "MNQ",
  "qty": 1,
  "tick_size": 0.25,
  "take_profit_ticks": 40,
  "stop_loss_ticks": 20,
  "max_hold_ms": 180000,
  "cooldown_ms": 120000,
  "max_consecutive_losses": 2,
  "max_daily_loss_usd": 50,
  "max_actions_per_session": 6,
  "session_windows": [
    { "start": "16:30:00", "end": "17:30:00", "tz": "Asia/Nicosia" }
  ],
  "use_pre_entry_cancel_all": true,
  "halt_on_stream_break": true,
  "halt_on_failed_execution": true
}
```

These defaults should be easy to override.

---

## Logging and audit requirements
The bot must produce enough evidence for post-trade debugging.

### Persist these logs
1. raw OCR attempts for accepted/rejected frames
2. accepted price ticks
3. stream health transitions
4. level detections
5. entry and exit reasons
6. all execution intents
7. all execution acknowledgements
8. halt reasons
9. end-of-session summary

### Screenshots to save
Save screenshots on:
- startup verification failure
- every live action attempt
- execution ack failure
- stream break
- halt event

### Suggested filenames
```text
2026-04-17_16-31-04.123_buy_intent.png
2026-04-17_16-31-04.455_buy_ack.png
2026-04-17_16-44-10.010_halt_overlay_detected.png
```

---

## Failure handling
The full bot must fail closed.

### Failure classes

#### 1. Price stream failure
Examples:
- OCR unreadable
- stale frames
- absurd price jumps
- anchor mismatch

Action:
- mark stream broken
- stop signal generation
- if in position and uncertain, emit `CANCEL_ALL`
- halt

#### 2. Execution failure
Examples:
- click point out of bounds
- window not focused
- acknowledgement missing
- screen signature mismatch

Action:
- publish failed ack
- emit `CANCEL_ALL` only if safe and configured
- halt

#### 3. Strategy/risk failure
Examples:
- daily loss exceeded
- max consecutive losses exceeded
- too many actions
- session window closed

Action:
- set state to `HALTED` for the session
- no more entries

#### 4. Local state uncertainty
Examples:
- duplicate actions fired
- entry ack unclear
- exit action unclear
- unknown current position assumption

Action:
- `CANCEL_ALL`
- halt
- manual intervention required

---

## Suggested implementation order
The coding LLM should implement in this order.

### Phase 1 — Calibration tools
Build:
- region/point picker
- config save/load
- reference screenshot capture

Deliverable:
- stable `screen_map.json`

### Phase 2 — Price reader
Build:
- fast region capture
- preprocessing variants
- OCR parser
- validator
- stream health tracker

Deliverable:
- console output of trusted MNQ prices

### Phase 3 — Paper signal engine
Build:
- tick buffer
- micro-bars
- level detector
- sweep detector
- risk manager
- intent emission without execution

Deliverable:
- paper signals logged only

### Phase 4 — Execution layer
Build:
- click driver
- hotkey fallback
- screen guard
- ack detector

Deliverable:
- test mode buttons for buy/sell/cancel-all

### Phase 5 — Full integration in paper mode
Build:
- event bus
- supervisor
- end-to-end startup sequence

Deliverable:
- price -> signal -> fake execution flow

### Phase 6 — Armed live mode
Build:
- execution arming switch
- action lock
- halt logic
- session summaries

Deliverable:
- full live bot

---

## Supervisor responsibilities
A top-level supervisor should own system-wide safety.

### Supervisor duties
- start all modules in order
- monitor worker heartbeats
- restart non-critical loops only if safe
- halt entire bot on critical safety violations
- expose clear current status to user

### Status values
```python
bot_status = one_of(
    "starting",
    "warmup",
    "paper_shadow",
    "armed_live_ready",
    "in_trade",
    "cooldown",
    "halted",
    "fatal"
)
```

---

## Suggested minimal UI for the bot itself
v1 can be a local desktop window or terminal UI.

### Show at minimum
- current bot mode
- current OCR price
- last accepted price timestamp
- stream health
- current local position
- current active level(s)
- last signal reason
- last execution ack
- consecutive losses
- daily estimated PnL
- halt reason if halted

### Manual controls
Provide buttons for:
- calibrate
- start paper mode
- arm live mode
- disarm
- manual cancel-all
- reset halted state

---

## Critical invariants
The full system must preserve these invariants.

1. Never place a new entry when local position is not flat.
2. Never send two actions at once.
3. Never execute if screen guard fails.
4. Never consume unvalidated OCR output as price.
5. Never continue after uncertain state.
6. Never ignore configured daily halt rules.
7. Never trade outside allowed time windows.
8. Never let execution module invent signals.
9. Never let signal engine bypass execution ack handling.
10. Always prefer halting over guessing.

---

## Example runtime timeline

```text
16:29:30  Bot starts
16:29:31  Config loaded
16:29:31  Screen verified
16:29:32  Price region OCR healthy
16:29:40  Warmup collecting bars
16:30:00  Signal engine READY
16:34:12  Resistance detected
16:34:20  Price sweeps above resistance
16:34:22  Price re-enters range
16:34:22  Intent emitted: SELL
16:34:22  Execution click sent
16:34:22  Ack accepted
16:34:22  Local position -> short
16:35:05  Target reached by price stream
16:35:05  Intent emitted: BUY (close short)
16:35:05  Execution click sent
16:35:06  Ack accepted
16:35:06  Local position -> flat
16:35:06  Cooldown active
```

---

## What the coding LLM should optimize for
The implementation should optimize for:
1. stability,
2. safety,
3. deterministic behavior,
4. debuggability,
5. simplicity of calibration,
6. correct halting behavior,
7. low-latency enough for MNQ screen trading.

Do **not** optimize first for elegance or abstraction depth.
The first successful version should be practical, inspectable, and conservative in failure cases.

---

## Final build target
The final deliverable should be a Python application that can:
- calibrate to one fixed Tradovate screen,
- read MNQ price from that screen in near real time,
- generate deterministic entry/exit signals from configured rules,
- click only buy/sell/cancel-all,
- halt safely whenever confidence is insufficient.

That is the correct v1 target.
