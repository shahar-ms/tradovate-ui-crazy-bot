# 05 — Orchestrator, Runtime Safety, and Runbook

## Why this file exists
This is the **fifth and final file to implement**. It wires the previous four files into one running bot.

By the time this file starts, the coder should already have:
- file 01: foundation and calibration
- file 02: stable price reader
- file 03: safe execution adapter
- file 04: replay-tested signal engine

This file is where the bot becomes a single usable program.

---

## What this file must do
The orchestrator must:
- start components in the correct order
- connect them with queues or message passing
- expose runtime modes
- handle halts and errors cleanly
- persist logs and state
- make it obvious when the bot is safe to arm and when it is not

This file should behave like a small runtime supervisor.

---

## Technical context
Python’s `queue` and `threading` libraries are enough for a clean v1 design using one process with several worker threads. The standard queue implementation is synchronized and designed for multi-producer/multi-consumer coordination, and Python logging supports queue-based handlers when logging should not block performance-sensitive threads. citeturn123605search4turn123605search9turn123605search3turn123605search13

That means the orchestrator can safely use a simple architecture:
- capture thread
- strategy thread
- execution thread
- supervisor/main thread

No distributed system is needed.

---

## Recommended runtime modes
Implement explicit modes:

1. `CALIBRATION`
2. `PRICE_DEBUG`
3. `PAPER`
4. `ARMED`
5. `HALTED`

### Meaning
- `CALIBRATION` — only file 01 tools
- `PRICE_DEBUG` — live price stream, no strategy, no clicks
- `PAPER` — live price + strategy + fake execution only
- `ARMED` — live price + strategy + real execution
- `HALTED` — no trading actions allowed until manual reset

This state machine must be very obvious in logs and console output.

---

## Recommended internal modules
Create these files inside `app/orchestrator/`:

```text
app/orchestrator/
  runtime_models.py
  supervisor.py
  event_bus.py
  bootstrap.py
  watchdogs.py
  commands.py
  runbot.py
```

---

## Core models to create

### RuntimeState
Fields:
- `mode`
- `session_id`
- `started_at_ts_ms`
- `armed`
- `halted`
- `halt_reason`
- `last_price_tick_ts_ms`
- `last_execution_ack_ts_ms`
- `current_position_side`
- `last_intent`

### RuntimeCommand
Fields:
- `command` (`pause`, `resume`, `halt`, `arm`, `disarm`, `cancel_all`)
- `ts_ms`
- `metadata`

### ComponentHealth
Fields:
- `price_stream_health`
- `execution_health`
- `strategy_health`
- `anchor_guard_ok`

---

## Event flow design
Use queues.

Suggested queues:
- `price_queue`
- `intent_queue`
- `ack_queue`
- `command_queue`

Flow:
1. file 02 produces `PriceTick`
2. file 04 consumes `PriceTick`, emits `SignalIntent`
3. file 03 consumes `SignalIntent`, emits `ExecutionAck`
4. supervisor consumes everything and decides whether to keep running or halt

Keep messages explicit and typed.

---

## Bootstrap sequence
Implement `bootstrap.py`.

Startup must happen in this order:

1. load config files
2. initialize logging
3. verify monitor exists
4. verify calibration files exist
5. validate anchor region
6. initialize price reader
7. initialize strategy engine
8. initialize execution adapter
9. start in safe mode (`PRICE_DEBUG` or `PAPER`), not `ARMED`

Do not allow direct startup into real trading mode without explicit arm step.

---

## Supervisor responsibilities
Implement `supervisor.py`.

The supervisor should:
- own the runtime mode
- watch component health
- halt on unsafe conditions
- print a concise status line regularly
- persist session state

Mandatory halt conditions:
- anchor guard fails
- price stream health becomes `broken`
- execution ack for entry is `unknown`
- config missing or corrupted
- queue backlog exceeds configured threshold
- operator sends halt command

When halted:
- no new entry actions
- optional manual `cancel_all` still allowed
- clear halt reason in UI/logs

---

## Watchdogs
Implement `watchdogs.py`.

At minimum include:

### Price watchdog
Halt if:
- no accepted price for too long
- health state broken

### Anchor watchdog
Periodically re-check anchor crop similarity.
If it drifts below threshold, halt.

### Execution watchdog
Track consecutive `unknown` or `failed` acknowledgements.
If they exceed threshold, halt.

### Queue watchdog
If one component falls behind badly, halt or degrade mode.

---

## Operator commands
Implement `commands.py`.

Required commands:
- `arm`
- `disarm`
- `halt`
- `resume_from_halt`
- `cancel_all`
- `status`

For v1, commands can come from:
- terminal input
- small local HTTP endpoint on localhost
- simple command file poller

The simplest is terminal input. Keep it boring.

---

## Console status line
Every few seconds, print something like:

```text
MODE=PAPER | PRICE=19234.25 | PRICE_HEALTH=ok | POS=flat | LAST_INTENT=SELL | LAST_ACK=ok | HALT_REASON=-
```

This matters a lot in practice. The operator must know what the bot thinks is happening.

---

## Persistence
Persist these artifacts:
- session log file
- price tick log
- execution log
- signal log
- current runtime state JSON
- sample screenshots on errors

This allows later debugging without guessing.

---

## Minimal `runbot.py` structure
```python
load_all_configs()
setup_logging()
bootstrap_all_components()
state = RuntimeState(mode="PRICE_DEBUG", ...)

start_price_thread()
start_strategy_thread()
start_execution_thread()
start_watchdogs()

while True:
    process_commands()
    refresh_runtime_state()
    print_status_line()
    if state.halted:
        enforce_halt_behavior()
```

---

## Recommended milestone path after coding file 05
Follow this exact progression:

### Stage 1 — calibration verification
- run only file 01 validator

### Stage 2 — price debug
- run live price reader only
- no strategy
- no clicks

### Stage 3 — paper mode
- live price
- live strategy
- fake execution only
- inspect emitted intents

### Stage 4 — sim mode with real clicks
- supervised Tradovate sim account
- cancel-all first
- then isolated buy/sell tests

### Stage 5 — armed small-size runtime
- one contract only
- human watching screen
- short session only
- halt on any uncertainty

Tradovate’s documented sim-account workflows and risk-setting tools make staged validation the right path instead of jumping straight into live automation. citeturn324338search16turn324338search17

---

## What not to build in file 05
Do not redesign earlier components here.

This file should not re-implement:
- OCR
- click logic
- level detection
- calibration UI

It should only **wire existing components together** and supervise them safely.

---

## Acceptance criteria
File 05 is complete only when:
- the bot can run end-to-end in `PAPER` mode
- `ARMED` mode requires an explicit operator action
- halt conditions work and stop new entries
- status output is clear
- logs and runtime state persist to disk

---

## Final instruction to the coding LLM
Your task here is to build the **runtime shell** around the earlier components. Keep it simple, observable, and hard to misread. A bot that halts clearly is better than a bot that keeps running in confusion.
