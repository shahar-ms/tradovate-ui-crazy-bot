# Part 2 — Signal Engine from Price Stream with Configurable Entry/Exit

## Objective
Build a **deterministic signal engine** that consumes the local MNQ price stream from Part 1 and emits only three intent types for Part 3:

- `BUY`
- `SELL`
- `CANCEL_ALL`

The engine must allow configuration of the main trading parameters such as:
- stop loss,
- take profit,
- cooldown,
- maximum losses,
- time windows,
- sweep thresholds,
- entry confirmation,
- emergency stop rules.

This document is written for implementation, not theory.

---

## Important framing
The software objective is **not** “guarantee tripling the account.”

The software objective is:
1. read a validated local price stream,
2. detect a specific high-risk rule set consistently,
3. enforce hard risk limits,
4. emit explicit intents to the execution layer,
5. stop trading when conditions are poor.

The user wants an aggressive/high-risk setup. Therefore this spec supports aggressive defaults, but the engine must still be configurable and fail closed.

---

## Chosen v1 strategy
Use a **price-only liquidity sweep / failed-breakout strategy**.

### Why this strategy
It fits the available data and execution constraints:
- works with price stream only,
- does not require API, order book, or volume,
- naturally maps to a state machine,
- entries are event-driven instead of constant,
- parameters are easy to expose to config.

### Core concept
A level is identified from recent price action. Price pushes through that level, then quickly returns back inside the range. That failed breakout is treated as the signal.

#### Short idea
- recent resistance exists
- price sweeps above it
- price quickly returns below it
- signal: `SELL`

#### Long idea
- recent support exists
- price sweeps below it
- price quickly returns above it
- signal: `BUY`

---

## Instrument assumptions
v1 supports MNQ only.

MNQ minimum tick size is **0.25 index points**, so every configurable threshold should be normalized internally in ticks. citeturn245392search2

Use:

```python
TICK_SIZE = 0.25
TICK_VALUE_USD = 0.50

def points_to_ticks(points: float) -> int:
    return round(points / TICK_SIZE)

def ticks_to_points(ticks: int) -> float:
    return ticks * TICK_SIZE
```

---

## Strategy mode
This engine should support one enabled strategy for v1:

```python
strategy_mode = "liquidity_sweep"
```

No multi-strategy blending in v1.

---

## Architecture

```text
signal_engine/
  app.py
  config.py
  models.py
  clock.py
  tick_buffer.py
  bar_builder.py
  level_detector.py
  sweep_detector.py
  entry_rules.py
  position_manager.py
  risk_manager.py
  session_gate.py
  intent_bus.py
  audit_log.py
```

---

## Data contracts

### Input tick

```python
@dataclass
class PriceTick:
    ts_local: str
    root_symbol: str
    price: float
    confidence: float
    source_mode: str
    sequence: int
```

### Output intent

```python
from dataclasses import dataclass
from typing import Literal, Optional

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
```

---

## High-level state machine

```text
BOOT
 -> WARMUP
 -> READY
 -> PRE_ENTRY_CLEANUP
 -> ENTRY_PENDING
 -> IN_POSITION
 -> EXIT_PENDING
 -> COOLDOWN
 -> READY
 -> HALTED
```

### Meaning
- `BOOT`: engine startup
- `WARMUP`: collecting enough history to detect levels
- `READY`: eligible to look for setups
- `PRE_ENTRY_CLEANUP`: emits `CANCEL_ALL` before entry if configured
- `ENTRY_PENDING`: a buy/sell intent was sent
- `IN_POSITION`: one position assumed active
- `EXIT_PENDING`: engine sent exit intent
- `COOLDOWN`: temporary post-trade pause
- `HALTED`: no more entries this session

---

## Position model
Keep this strict for v1.

```python
@dataclass
class PositionState:
    side: Literal["flat", "long", "short"] = "flat"
    qty: int = 0
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    opened_at: str | None = None
    last_intent_id: str | None = None
```

### v1 restrictions
- only 1 open position at a time
- only 1 entry signal at a time
- no pyramiding
- no martingale
- no averaging down
- no same-tick reversal

---

## Bar model
The engine should derive micro-bars from the incoming tick stream.

Recommended defaults:
- `micro_bar_ms = 5000`
- `level_lookback_bars = 12`
- `warmup_bars = 20`

Bar schema:

```python
@dataclass
class MicroBar:
    start_ts: str
    end_ts: str
    open: float
    high: float
    low: float
    close: float
```

---

## Level detection
The engine should detect candidate support/resistance levels using recent bars.

### Rule set
A level is considered valid when:
1. it has been touched at least `min_level_touches` times,
2. touches are within `level_tolerance_ticks`,
3. range height is between configured min and max,
4. level is based on recent bars only.

### Practical v1 simplification
For each evaluation window:
- resistance = highest repeated swing area in last `level_lookback_bars`
- support = lowest repeated swing area in last `level_lookback_bars`

Do not overcomplicate this. The strategy needs consistency more than sophistication.

---

## Entry rules — liquidity sweep

### Short entry
Emit a `SELL` only if all conditions hold:
1. engine state is `READY`
2. session gate open
3. stream health is acceptable
4. local resistance exists
5. price trades above resistance by at least `min_sweep_distance_ticks`
6. price does not exceed `max_sweep_distance_ticks`
7. within `max_return_window_ms`, price returns back below the resistance level
8. price confirms re-entry by at least `confirm_ticks_inside_range`
9. no open position
10. risk manager allows a new trade

### Long entry
Emit a `BUY` only if all conditions hold:
1. engine state is `READY`
2. session gate open
3. stream health is acceptable
4. local support exists
5. price trades below support by at least `min_sweep_distance_ticks`
6. price does not exceed `max_sweep_distance_ticks`
7. within `max_return_window_ms`, price returns back above support
8. price confirms re-entry by at least `confirm_ticks_inside_range`
9. no open position
10. risk manager allows a new trade

---

## Recommended aggressive defaults
These defaults are aggressive because the user explicitly requested high risk, but they should remain configurable.

```json
{
  "instrument": {
    "root_symbol": "MNQ",
    "tick_size": 0.25,
    "tick_value_usd": 0.5
  },
  "stream_guard": {
    "min_reader_confidence": 0.84,
    "block_on_stalled_stream": true,
    "block_on_broken_stream": true,
    "reject_visual_drift": true
  },
  "bars": {
    "micro_bar_ms": 5000,
    "level_lookback_bars": 12,
    "warmup_bars": 20
  },
  "strategy": {
    "name": "liquidity_sweep",
    "min_level_touches": 2,
    "level_tolerance_ticks": 2,
    "min_sweep_distance_ticks": 6,
    "max_sweep_distance_ticks": 16,
    "max_return_window_ms": 10000,
    "confirm_ticks_inside_range": 2,
    "min_range_height_ticks": 14,
    "max_range_height_ticks": 120,
    "cancel_all_before_entry": true
  },
  "risk": {
    "contracts": 1,
    "stop_loss_ticks": 20,
    "take_profit_ticks": 48,
    "max_time_in_trade_ms": 120000,
    "cooldown_ms": 30000,
    "max_consecutive_losses": 2,
    "max_entries_per_session": 4,
    "daily_loss_usd": 30.0,
    "daily_profit_lock_usd": 120.0
  },
  "session": {
    "timezone": "America/New_York",
    "enabled": true,
    "trade_windows": [
      { "start": "09:30:00", "end": "10:30:00" },
      { "start": "11:00:00", "end": "12:00:00" }
    ]
  },
  "safety": {
    "block_after_news_mode": false,
    "pre_entry_cancel_all": true,
    "require_flat_before_new_entry": true,
    "halt_on_execution_mismatch": true,
    "intent_timeout_ms": 2500
  }
}
```

### What those defaults mean in dollars
- `20 ticks` stop = `5 points` = about **$10** on 1 MNQ
- `48 ticks` target = `12 points` = about **$24** on 1 MNQ

This keeps the setup aggressive but still structured.

---

## Risk manager rules
The risk manager is mandatory.

### Mandatory pre-entry checks
Block entry if any of these are true:
- stream unhealthy
- outside session window
- warmup incomplete
- current position not flat
- consecutive losses exceeded
- daily loss reached
- max entries reached
- last intent still unresolved

### Mandatory in-trade exits
Once in position, monitor continuously for:
- stop loss hit
- take profit hit
- max time in trade exceeded
- stream breaks badly while in trade
- explicit external halt request

### Emergency behavior
If in position and stream becomes unusable:
- emit `CANCEL_ALL`
- then emit opposite action as a market-style exit intent if your executor is using the buy/sell buttons to flatten side exposure
- then move to `HALTED`

Because Part 3 only supports `BUY`, `SELL`, and `CANCEL_ALL`, flattening must be represented as the opposite side action when already in a position.

---

## Entry flow

```text
READY
 -> detect valid sweep
 -> emit CANCEL_ALL if configured
 -> wait for cleanup acknowledgement or timeout
 -> emit BUY or SELL
 -> move to ENTRY_PENDING
 -> on execution ack -> IN_POSITION
 -> compute stop and target from actual/expected entry
```

---

## Exit logic

### For long positions
Exit conditions:
- current_price <= stop_price  -> emit `SELL`
- current_price >= target_price -> emit `SELL`
- elapsed_ms >= max_time_in_trade_ms -> emit `SELL`

### For short positions
Exit conditions:
- current_price >= stop_price -> emit `BUY`
- current_price <= target_price -> emit `BUY`
- elapsed_ms >= max_time_in_trade_ms -> emit `BUY`

### Optional breakeven extension
Keep disabled for v1 unless explicitly configured.

---

## Signal confirmation details
A sweep should not fire on the first violation alone.

### Short confirmation
1. price exceeds resistance by threshold
2. mark sweep candidate
3. start return timer
4. if price returns below resistance and then moves further inside by `confirm_ticks_inside_range`, confirm `SELL`

### Long confirmation
1. price exceeds support downside by threshold
2. mark sweep candidate
3. start return timer
4. if price returns above support and then moves further inside by `confirm_ticks_inside_range`, confirm `BUY`

This reduces some false positives.

---

## Internal objects

```python
@dataclass
class LevelState:
    support: float | None = None
    resistance: float | None = None
    support_touches: int = 0
    resistance_touches: int = 0

@dataclass
class SweepCandidate:
    side: Literal["long", "short"] | None = None
    level_price: float | None = None
    extreme_price: float | None = None
    started_at: str | None = None
    active: bool = False
```

---

## Intent reasons
Use explicit machine-readable reason strings.

### Entry reasons
- `ENTER_LONG_SWEEP`
- `ENTER_SHORT_SWEEP`
- `PRE_ENTRY_CLEANUP`

### Exit reasons
- `EXIT_LONG_STOP`
- `EXIT_LONG_TARGET`
- `EXIT_LONG_TIME`
- `EXIT_SHORT_STOP`
- `EXIT_SHORT_TARGET`
- `EXIT_SHORT_TIME`
- `EMERGENCY_STREAM_FAIL`
- `SESSION_HALT`

---

## Execution acknowledgement handling
The signal engine should never assume execution succeeded without an acknowledgement from Part 3.

### On entry ack success
- move to `IN_POSITION`
- initialize stop/target
- start trade timer

### On entry ack failure
- move to `HALTED` or `READY` depending on config
- strongly recommended default: `HALTED`

### On exit ack success
- update PnL estimate
- increment counters
- move to `COOLDOWN`

### On exit ack failure
- emit `CANCEL_ALL`
- halt the engine

---

## Logging requirements
Every signal decision should be logged with:
- timestamp
- current state
- current price
- support/resistance values
- sweep candidate status
- reason for entry or skip
- stop and target values
- intent id
- execution ack result

This is essential for debugging and post-session review.

---

## Pseudocode

```python
if state == "READY":
    if not session_gate.can_trade(now):
        return
    if not risk_manager.can_enter():
        return
    if not stream_guard.ok(latest_tick):
        return

    levels = level_detector.compute(recent_bars)
    candidate = sweep_detector.update(levels, latest_tick.price)

    if candidate.confirmed_short:
        if config.safety.pre_entry_cancel_all:
            emit_intent("CANCEL_ALL", reason="PRE_ENTRY_CLEANUP")
            state = "PRE_ENTRY_CLEANUP"
        else:
            emit_intent("SELL", reason="ENTER_SHORT_SWEEP")
            state = "ENTRY_PENDING"

    elif candidate.confirmed_long:
        if config.safety.pre_entry_cancel_all:
            emit_intent("CANCEL_ALL", reason="PRE_ENTRY_CLEANUP")
            state = "PRE_ENTRY_CLEANUP"
        else:
            emit_intent("BUY", reason="ENTER_LONG_SWEEP")
            state = "ENTRY_PENDING"
```

---

## Suggested implementation order
1. ingest ticks
2. build micro-bars
3. detect levels
4. detect sweep candidates
5. implement state machine
6. implement stop/target handling
7. implement cooldown and session gates
8. wire execution acknowledgements
9. add structured logs and replay capability

---

## Implementation note for the coding LLM
Keep this engine **strictly deterministic**. No randomization, no adaptive ML, no hidden heuristics in v1. The user needs something debuggable and automatable fast, not something opaque.
