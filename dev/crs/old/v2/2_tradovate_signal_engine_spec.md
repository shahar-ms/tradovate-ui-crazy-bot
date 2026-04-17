# Price-Only Signal Engine Spec for MNQ on Tradovate UI Automation

## Purpose
Build a deterministic signal engine that consumes a local real-time price stream and produces only three execution intents for the UI execution layer:

- `BUY`
- `SELL`
- `CANCEL_ALL`

The signal engine must support configurable basic parameters such as TP, SL, cooldowns, time windows, and safety limits.

This document is intentionally detailed so a coding LLM can implement it with minimal ambiguity.

---

## Important framing
This is a **spec for an experimental rule-based trading engine**, not a promise of profit. A target like “triple the account quickly” is not a software requirement that can be guaranteed by code. The correct software objective is:

> produce deterministic entry/exit signals from a validated price stream, enforce hard risk constraints, and fail closed when the environment is uncertain.

---

## Scope
### In scope
- Consume real-time price ticks from part 1.
- Aggregate ticks into derived micro-bars and event windows.
- Run one default signal model for price-only trading.
- Manage position state.
- Emit execution intents and protective-state transitions.
- Support configuration via JSON.

### Out of scope
- Direct interaction with Tradovate UI.
- Account balance polling from broker.
- Any claim of profitability.
- Use of volume, DOM depth, or broker API fields.

---

## Chosen v1 strategy
Use a **price-only liquidity sweep / failed-breakout model** because:
- it can be implemented from price stream alone,
- it is event-driven,
- it avoids constant overtrading,
- it naturally fits a state machine,
- it is configurable without new data sources.

### Core idea
Enter only when price briefly breaks a clearly defined local level and then re-enters the prior range quickly.

### Short setup
1. Local resistance level is identified.
2. Price trades above that level by a configurable sweep distance.
3. Within a configurable return window, price falls back below the level.
4. Engine emits `SELL`.

### Long setup
1. Local support level is identified.
2. Price trades below that level by a configurable sweep distance.
3. Within a configurable return window, price rises back above the level.
4. Engine emits `BUY`.

---

## MNQ-specific normalization
MNQ contract specs list a minimum price fluctuation of **0.25 index points**, equal to **$0.50 per tick**. Therefore every parameter should be representable both in points and in ticks, and the engine should normalize them internally to ticks.

### Helpers
```ts
const TICK_SIZE = 0.25;
const TICK_VALUE_USD = 0.50; // per 1 MNQ

function pointsToTicks(points: number): number {
  return Math.round(points / TICK_SIZE);
}

function ticksToPoints(ticks: number): number {
  return ticks * TICK_SIZE;
}
```

---

## System architecture

```text
signal-engine/
  src/
    app.ts
    config/
      schema.ts
    bus/
      price-stream-client.ts
      intent-publisher.ts
    models/
      tick.ts
      micro-bar.ts
      position-state.ts
      signal.ts
      risk-state.ts
    bars/
      bar-builder.ts
    levels/
      swing-detector.ts
      local-range-model.ts
    strategy/
      liquidity-sweep.ts
    risk/
      risk-guard.ts
      stop-target-manager.ts
      session-guard.ts
    state/
      engine-store.ts
    logs/
      audit-log.ts
```

---

## Input contract from price reader

```ts
export type PriceTick = {
  tsLocal: string;
  instrument: string;
  rootSymbol: string;
  price: number;
  sourceMode: "dom-text" | "dom-mutation" | "visual-fallback";
  confidence: number;
  sequence: number;
};
```

The engine must reject any tick where:
- `rootSymbol !== expectedRootSymbol`
- `confidence < minReaderConfidence`
- price is not aligned to tick size
- sequence regresses

---

## Output contract to execution layer

```ts
export type ExecutionIntent = {
  intentId: string;
  tsLocal: string;
  action: "BUY" | "SELL" | "CANCEL_ALL";
  reason:
    | "ENTER_LONG_SWEEP"
    | "ENTER_SHORT_SWEEP"
    | "EXIT_LONG_STOP"
    | "EXIT_LONG_TARGET"
    | "EXIT_SHORT_STOP"
    | "EXIT_SHORT_TARGET"
    | "TIME_STOP"
    | "SESSION_HALT"
    | "HEALTH_HALT"
    | "PRE_ENTRY_CLEANUP";
  qty: number;
  expectedPrice?: number;
  positionEffect: "open" | "close" | "reduce" | "flat_cleanup";
  strategyStateSnapshot: Record<string, unknown>;
};
```

---

## High-level state machine

```text
BOOT
  -> WARMUP
  -> READY
  -> PENDING_ENTRY
  -> IN_POSITION
  -> COOLDOWN
  -> READY
  -> HALTED
```

### State meanings
- `BOOT`: engine starting.
- `WARMUP`: collecting enough price history to form levels.
- `READY`: can evaluate entries.
- `PENDING_ENTRY`: signal emitted, waiting for execution confirmation from execution layer.
- `IN_POSITION`: engine believes one position is open.
- `COOLDOWN`: temporary no-trade state after exit.
- `HALTED`: hard stop; no new entries.

---

## Position model
For v1, keep it simple:
- only **one open position at a time**,
- only **one unit size configuration**,
- no pyramiding,
- no averaging down,
- no reversing in the same tick.

```ts
export type PositionState = {
  side: "flat" | "long" | "short";
  qty: number;
  entryPrice?: number;
  stopPrice?: number;
  targetPrice?: number;
  openedAt?: string;
  lastIntentId?: string;
};
```

---

## Config schema

```json
{
  "instrument": {
    "rootSymbol": "MNQ",
    "tickSize": 0.25,
    "tickValueUsd": 0.5
  },
  "readerValidation": {
    "minReaderConfidence": 0.85,
    "rejectVisualFallbackEntries": true
  },
  "bars": {
    "microBarMs": 5000,
    "levelLookbackBars": 12,
    "minWarmupBars": 20
  },
  "strategy": {
    "enabled": "liquidity_sweep",
    "minLevelTouches": 2,
    "levelToleranceTicks": 2,
    "minSweepDistanceTicks": 6,
    "maxSweepDistanceTicks": 24,
    "maxReturnWindowMs": 12000,
    "confirmTicksInsideRange": 2,
    "minRangeHeightTicks": 16,
    "maxRangeHeightTicks": 120
  },
  "risk": {
    "contracts": 1,
    "stopLossTicks": 10,
    "takeProfitTicks": 24,
    "maxTimeInTradeMs": 120000,
    "cooldownMs": 30000,
    "maxConsecutiveLosses": 2,
    "dailyLossUsd": 30,
    "maxEntriesPerSession": 4,
    "cancelAllBeforeEntry": true
  },
  "session": {
    "enabled": true,
    "timezone": "America/New_York",
    "tradeWindows": [
      { "start": "09:30:00", "end": "10:30:00" },
      { "start": "11:00:00", "end": "12:00:00" }
    ]
  }
}
```

---

## Level construction
The engine needs local support/resistance from price only.

### Micro-bar builder
Aggregate ticks into short bars, for example 5-second bars:
- open
- high
- low
- close
- start/end time

### Swing detection
A simple level candidate is created when repeated local highs or lows occur within `levelToleranceTicks`.

#### Resistance candidate
- at least `minLevelTouches` highs clustered within tolerance
- built from the last `levelLookbackBars`

#### Support candidate
- at least `minLevelTouches` lows clustered within tolerance
- built from the last `levelLookbackBars`

### Range quality filters
Reject a level set if:
- range height is too small
- range height is too large
- warmup is incomplete
- price reader health is degraded

---

## Entry logic in detail

### Short entry: failed breakout above resistance
Variables:
- `R` = active resistance level
- `sweepHigh` = highest traded price after first break above `R`

Conditions:
1. Engine is `READY`.
2. Resistance level `R` exists and is valid.
3. Current price trades above `R` by at least `minSweepDistanceTicks`.
4. It does **not** exceed `maxSweepDistanceTicks`.
5. Within `maxReturnWindowMs`, price returns back below `R`.
6. Price remains below `R` for `confirmTicksInsideRange` valid ticks.
7. Emit `CANCEL_ALL` if configured.
8. Emit `SELL`.

### Long entry: failed breakout below support
Mirror logic:
1. Engine is `READY`.
2. Support level `S` exists.
3. Price trades below `S` by at least `minSweepDistanceTicks`.
4. It does not exceed `maxSweepDistanceTicks`.
5. Within `maxReturnWindowMs`, price returns above `S`.
6. Holds above `S` for `confirmTicksInsideRange` valid ticks.
7. Emit `CANCEL_ALL` if configured.
8. Emit `BUY`.

---

## Stop, target, and exit logic
Because the execution layer only exposes `BUY`, `SELL`, and `CANCEL_ALL`, the engine must treat **opposite-side market action** as the close method.

### If long
- stop breach -> emit `SELL`
- target breach -> emit `SELL`
- max time in trade exceeded -> emit `SELL`

### If short
- stop breach -> emit `BUY`
- target breach -> emit `BUY`
- max time in trade exceeded -> emit `BUY`

### Stop/target calculation
At entry:
- long stop = `entryPrice - stopLossTicks * tickSize`
- long target = `entryPrice + takeProfitTicks * tickSize`
- short stop = `entryPrice + stopLossTicks * tickSize`
- short target = `entryPrice - takeProfitTicks * tickSize`

### Exit precedence
If multiple exit conditions trigger on the same evaluation cycle, use this order:
1. hard risk / session halt
2. stop loss
3. take profit
4. time stop

---

## Risk guardrails
These are mandatory software constraints, not optional trading preferences.

### Per-trade constraints
- exactly one position at a time
- fixed quantity
- stop and target defined before state becomes `IN_POSITION`

### Session constraints
- no entries outside allowed windows
- no entries if reader health is not `healthy`
- no entries if consecutive losses reached threshold
- no entries if estimated daily loss limit reached
- no entries after `maxEntriesPerSession`

### Environment constraints
Halt entries if:
- tick stream is `degraded`, `stalled`, or `broken`
- execution layer fails to confirm last command
- visible instrument mismatches expected instrument
- price gaps exceed sanity threshold repeatedly

---

## Execution acknowledgement model
Although the engine does not click the UI itself, it should expect an acknowledgement from the execution layer.

### Why
Without broker API confirmation, the system can desynchronize between:
- intended state
- actual UI state
- actual broker state

### Minimal required acknowledgement
The execution layer should respond with:

```ts
export type ExecutionAck = {
  intentId: string;
  ok: boolean;
  action: "BUY" | "SELL" | "CANCEL_ALL";
  tsLocal: string;
  message?: string;
  observedPositionAfter?: "flat" | "long" | "short" | "unknown";
};
```

If `ok=false` or no ack arrives within timeout:
- mark engine `HALTED`
- emit audit log
- do not continue trading blindly

---

## Internal algorithm details

### On each incoming tick
1. validate tick
2. update health snapshot
3. append tick to ring buffer
4. update current micro-bar
5. if a bar closed, update level model
6. evaluate state-specific rules
7. possibly emit intent
8. write audit log

### Pseudocode
```ts
onTick(tick) {
  if (!isValidTick(tick)) return;
  if (!sessionGuard.allowsNow()) return maybeHaltIfNeeded();
  if (!riskGuard.readerHealthy()) return haltEntries();

  bars.ingest(tick);
  levels.update(bars);

  switch (state.mode) {
    case "WARMUP":
      if (levels.ready()) state.mode = "READY";
      break;

    case "READY":
      const signal = strategy.evaluateEntry({ tick, levels, state, config });
      if (signal) {
        if (config.risk.cancelAllBeforeEntry) emitCancelAll(signal);
        emitEntry(signal);
        state.mode = "PENDING_ENTRY";
      }
      break;

    case "IN_POSITION":
      const exit = stopTargetManager.evaluateExit({ tick, position, config });
      if (exit) emitExit(exit);
      break;

    case "COOLDOWN":
      if (cooldownExpired()) state.mode = "READY";
      break;
  }
}
```

---

## Strategy evaluation pseudocode
```ts
function evaluateShortSweep(ctx): Signal | null {
  const R = ctx.levels.activeResistance;
  if (!R) return null;

  const brokeAbove = ctx.price >= R + ticksToPoints(cfg.minSweepDistanceTicks);
  const notTooFar = ctx.price <= R + ticksToPoints(cfg.maxSweepDistanceTicks);

  if (brokeAbove && notTooFar) {
    rememberSweepEvent("aboveResistance", ctx.tick);
  }

  const sweep = getActiveSweepEvent("aboveResistance");
  if (!sweep) return null;

  const withinWindow = ctx.nowMs - sweep.startedAtMs <= cfg.maxReturnWindowMs;
  const backInside = ctx.price < R;

  if (withinWindow && backInside && heldInsideForTicks(cfg.confirmTicksInsideRange)) {
    return {
      side: "short",
      reason: "ENTER_SHORT_SWEEP",
      entryRefPrice: ctx.price
    };
  }

  if (!withinWindow) expireSweepEvent(sweep);
  return null;
}
```

The long side is the symmetric opposite.

---

## Trade accounting model
Even without broker API, keep an internal PnL estimate for session guardrails.

### Estimated realized PnL
For 1 MNQ:
```ts
pnlUsd = priceDeltaPoints * 2.0;
```

Since CME lists MNQ as **$2 x Nasdaq-100 Index** and a **0.25 point minimum tick = $0.50**, this estimation is straightforward for one-contract internal risk tracking.

Use this estimate only for internal safety counters, not for official accounting.

---

## Suggested default parameters for first live experiments
These are not “best” parameters. They are just a coherent starting set.

```json
{
  "strategy": {
    "minLevelTouches": 2,
    "levelToleranceTicks": 2,
    "minSweepDistanceTicks": 6,
    "maxSweepDistanceTicks": 18,
    "maxReturnWindowMs": 10000,
    "confirmTicksInsideRange": 2,
    "minRangeHeightTicks": 20,
    "maxRangeHeightTicks": 80
  },
  "risk": {
    "contracts": 1,
    "stopLossTicks": 10,
    "takeProfitTicks": 24,
    "maxTimeInTradeMs": 90000,
    "cooldownMs": 30000,
    "maxConsecutiveLosses": 2,
    "dailyLossUsd": 30,
    "maxEntriesPerSession": 3,
    "cancelAllBeforeEntry": true
  }
}
```

---

## Mandatory audit logging
Every material decision must be logged.

### Examples
```json
{"event":"engine_state","from":"WARMUP","to":"READY"}
{"event":"level_detected","support":21402.25,"resistance":21420.75}
{"event":"signal_candidate","side":"short","reason":"failed_breakout","resistance":21420.75}
{"event":"intent_emitted","action":"SELL","reason":"ENTER_SHORT_SWEEP","intentId":"..."}
{"event":"position_opened","side":"short","entryPrice":21419.75,"stopPrice":21422.25,"targetPrice":21413.75}
{"event":"position_closed","side":"short","reason":"EXIT_SHORT_TARGET","estimatedPnlUsd":12.0}
{"event":"halt","reason":"reader_health_degraded"}
```

---

## Test plan

### Unit tests
- tick validation
- bar building
- swing clustering
- sweep detection
- stop loss triggering
- take profit triggering
- time stop
- cooldown behavior
- daily loss guard

### Simulation tests using recorded tick files
1. replay historical/recorded price stream
2. verify deterministic signals
3. verify no duplicate intent for same setup
4. verify halt on degraded reader events

### Paper/live-sim tests
- connect to Tradovate simulation first
- verify entries only in allowed windows
- verify exits occur when thresholds are crossed
- verify engine halts after configured consecutive losses

### Acceptance criteria
- same input tick file always produces identical intents
- no entry when reader confidence is below threshold
- no entry while already in position
- every open position has stop and target recorded locally
- every exit produces exactly one close-side intent

---

## Engineering guidance to the coding LLM
- Implement the strategy as a pure function over state where possible.
- Keep configuration separate from code.
- Make all numeric thresholds explicit and configurable.
- Keep strategy logic independent from Tradovate UI specifics.
- Treat uncertainty as a reason to halt, not a reason to guess.
- Optimize for determinism and observability before optimization for speed.

---

## Suggested first milestone
1. Read live price ticks from part 1.
2. Build micro-bars.
3. Detect support/resistance clusters.
4. Emit paper signals only, without execution.
5. Add position/risk state.
6. Add intent output with acknowledgement timeout handling.

Only then connect to real UI clicking.

---

## Source notes used for this design
- CME contract specs for MNQ tick size/value.
- Tradovate public material confirming simulation availability and the lack of API access below the required threshold.
