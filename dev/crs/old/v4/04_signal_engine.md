# 04 — Signal Engine from Price Stream

## Why this file exists
This is the **fourth file to implement**. It converts the clean price stream from file 02 into entry and exit intents that can later be sent to the execution layer from file 03.

This file must remain independent from UI details. It should think only in terms of:
- price ticks
- derived bars
- local levels
- entry pattern
- exits
- guardrails
- emitted intents

---

## What this file assumes already exists
This file assumes:
- file 02 already outputs validated `PriceTick` objects
- file 03 already knows how to execute `BUY`, `SELL`, and `CANCEL_ALL`
- configs already exist from file 01

This file does **not** click anything directly.

---

## Trading context for this file
The user asked for a **high-risk**, short-horizon automation. That means the strategy in this file should be:
- simple enough to implement correctly
- based only on price stream, because no order-book API is available
- configurable, not hard-coded
- easy to replay-test

For v1, the best fit is a **price-only liquidity sweep / failed breakout strategy** around recently formed levels.

This is not designed as a low-risk production strategy. It is designed as a **testable first strategy** for a screen-automation bot that only has price.

---

## Strategy chosen for v1
### Liquidity sweep / failed breakout
Idea:
1. detect a recent short-term level from price action
2. wait for price to push through it
3. require price to return back through the level quickly
4. enter in the reversal direction
5. manage exit using configurable TP / SL / timeout

Why this strategy is a good fit here:
- uses only price
- works on short-term data
- easier to express as deterministic rules than discretionary chart reading
- can be replay-tested without UI automation

---

## Important limitation
This file should not promise profitability. It only defines a deterministic rules engine that the user can test. It must support:
- simulation
- replay
- config tuning
- halts on uncertainty

---

## Recommended internal modules
Create these files inside `app/strategy/`:

```text
app/strategy/
  models.py
  bar_builder.py
  levels.py
  signal_rules.py
  risk_manager.py
  state_machine.py
  engine.py
  replay.py
```

---

## Core models to create

### MicroBar
Fields:
- `start_ts_ms`
- `end_ts_ms`
- `open`
- `high`
- `low`
- `close`

### Level
Fields:
- `price`
- `kind` (`resistance`, `support`)
- `touch_count`
- `first_seen_ts_ms`
- `last_seen_ts_ms`

### SignalIntent
Fields:
- `intent_id`
- `action` (`BUY`, `SELL`, `CANCEL_ALL`, `EXIT_LONG`, `EXIT_SHORT`)
- `reason`
- `trigger_price`
- `ts_ms`
- `metadata`

### PositionState
Fields:
- `side` (`flat`, `long`, `short`)
- `entry_price`
- `entry_ts_ms`
- `stop_price`
- `target_price`
- `bars_in_trade`

### RiskState
Fields:
- `trades_today`
- `consecutive_losses`
- `last_exit_ts_ms`
- `halted`
- `halt_reason`

---

## Required config for this file
Use `strategy_config.json`.

Suggested structure:

```json
{
  "symbol": "MNQ",
  "tick_size": 0.25,
  "bar_seconds": 1,
  "level_lookback_bars": 120,
  "level_touch_tolerance_points": 0.5,
  "min_touches_for_level": 2,
  "sweep_break_distance_points": 1.0,
  "sweep_return_timeout_bars": 5,
  "entry_offset_points": 0.0,
  "stop_loss_points": 5.0,
  "take_profit_points": 12.0,
  "time_stop_bars": 20,
  "cooldown_bars_after_exit": 10,
  "max_trades_per_session": 6,
  "max_consecutive_losses": 2,
  "cancel_all_before_new_entry": true,
  "session_windows": [
    {"start": "16:30", "end": "18:30", "timezone": "Asia/Nicosia"}
  ]
}
```

Keep every trading parameter configurable.

---

## Implementation order inside this file
Build in this order:

1. micro-bar builder
2. level detection
3. state machine
4. entry pattern rules
5. stop/target logic
6. cooldown and risk guards
7. intent emission
8. replay test runner

---

## 1. Micro-bar builder
Implement `bar_builder.py`.

Input:
- validated `PriceTick` objects

Output:
- 1-second or configurable bars

Why do this:
- direct tick-to-strategy rules get messy quickly
- 1-second bars preserve short-term behavior while making level logic easier

Rules:
- first tick starts current bar
- later ticks update high/low/close
- bar closes when next interval starts
- missing-tick intervals should not invent bars unless explicitly configured

---

## 2. Level detection
Implement `levels.py`.

The first version should be simple and deterministic.

Suggested method:
- keep rolling recent micro-bars
- detect local swing highs and swing lows
- cluster nearby swings within tolerance
- treat repeated touches as candidate support/resistance levels

Example:
- if several local highs occur within 0.5 points of each other, form a resistance level
- if several local lows occur within 0.5 points of each other, form a support level

Store:
- level price
- touch count
- freshness

Do not overcomplicate level math in v1.

---

## 3. State machine
Implement `state_machine.py`.

States:
- `FLAT`
- `PENDING_ENTRY`
- `LONG`
- `SHORT`
- `PENDING_EXIT`
- `HALTED`

This explicit state machine is important. It prevents accidental double-entry and unclear transitions.

Core rules:
- only one position at a time in v1
- no entry if not `FLAT`
- no new entry if halted
- on uncertain execution result later, higher layer can force `HALTED`

---

## 4. Entry rule: sweep / failed breakout
Implement in `signal_rules.py`.

### Short entry pattern
1. a valid resistance level exists
2. current bar or recent tick pushes above resistance by at least `sweep_break_distance_points`
3. within `sweep_return_timeout_bars`, price closes back below the level
4. emit `CANCEL_ALL` first if configured
5. then emit `SELL`

### Long entry pattern
1. a valid support level exists
2. price pushes below support by required distance
3. within timeout, price closes back above the level
4. emit `CANCEL_ALL` first if configured
5. then emit `BUY`

### Important anti-noise filters
Require at least:
- enough touches on the level
- minimum recent range
- not in cooldown
- not beyond daily/session trade cap
- only during allowed session window

---

## 5. Stop loss, take profit, and time stop
Implement in `engine.py` or a dedicated manager.

When a position is open, create exit conditions using only the price stream.

### For long
- stop if `last_price <= stop_price`
- target if `last_price >= target_price`
- time stop if trade duration exceeds configured bar count

### For short
- stop if `last_price >= stop_price`
- target if `last_price <= target_price`
- time stop if trade duration exceeds configured bar count

The file should emit exit intents, not click directly.

---

## 6. Risk manager
Implement `risk_manager.py`.

Even though the user wants high risk, the strategy engine still needs hard brakes.

Required guards:
- max trades per session
- max consecutive losses
- cooldown after exit
- no entries when price stream health is degraded/broken
- optional daily stop after estimated P/L threshold

This engine should be aggressive in opportunity selection, not reckless in software behavior.

---

## 7. Intent emission contract
The strategy engine should output a very clean intent stream.

Examples:

```python
SignalIntent(action="CANCEL_ALL", reason="cleanup_before_short_entry")
SignalIntent(action="SELL", reason="resistance_sweep_reversal")
SignalIntent(action="EXIT_SHORT", reason="take_profit_hit")
```

Design rule:
- the strategy engine decides **what** should happen
- the execution layer decides **how** to click it

Keep these concerns separate.

---

## Replay runner
Implement `replay.py`.

This is mandatory.

It should:
- load tick logs recorded from file 02
- feed them through the engine
- output detected levels, entries, exits, and halt conditions
- allow config tuning without live clicks

This is where most debugging should happen.

---

## Pseudocode for core signal loop
```python
for tick in tick_stream:
    if not price_stream_health_ok:
        continue

    bar_builder.on_tick(tick)
    if not bar_builder.has_new_bar():
        continue

    bar = bar_builder.close_bar()
    levels.update(bar)
    risk.update_time(bar)

    if state.is_flat() and risk.can_enter():
        signal = rules.check_entry(levels, recent_bars, tick)
        if signal:
            if config.cancel_all_before_new_entry:
                emit(CANCEL_ALL)
            emit(signal)
            state.to_pending_entry(signal)

    elif state.is_long() or state.is_short():
        exit_signal = rules.check_exit(position, tick, bar)
        if exit_signal:
            emit(exit_signal)
            state.to_pending_exit(exit_signal)
```

---

## Unit tests to write
At minimum:
- bar builder creates correct OHLC from ticks
- nearby swing highs form one resistance level
- short sweep signal triggers only after break and return
- long sweep signal triggers only after break and return
- stop loss signal fires correctly
- take profit signal fires correctly
- cooldown blocks new entries
- max consecutive losses triggers halt

---

## Manual test plan
Before moving to file 05:

1. record live price data using file 02
2. run replay on several sessions
3. inspect levels and signals visually against charts
4. adjust strategy config only, not code, for tuning
5. confirm engine does not spam multiple entries while already in trade

---

## What not to build in file 04
Do not build:
- screen capture
- OCR
- mouse clicks
- browser logic
- full bot startup orchestration

This file is only about **turning price into deterministic trade intents**.

---

## Acceptance criteria
File 04 is complete only when:
- the engine consumes validated price ticks
- it emits deterministic entry/exit intents
- all core risk/config parameters are externalized
- replay mode exists and is useful
- it can run without any UI clicking enabled

---

## Handoff to file 05
File 05 will combine:
- price stream from file 02
- execution adapter from file 03
- strategy engine from file 04

and run them together safely.

---

## Final instruction to the coding LLM
Your task here is to build a **clean rule engine**, not a giant trading platform. Keep the logic explicit, configurable, replayable, and easy to reason about.
