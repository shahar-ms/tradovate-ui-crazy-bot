# Tradovate Web UI Execution Layer Spec

## Purpose
Build a browser automation component that performs only these three actions on the Tradovate web application:

- `BUY`
- `SELL`
- `CANCEL_ALL`

This execution layer receives intents from the signal engine and interacts with the Tradovate web UI in a deterministic, observable, and fail-closed manner.

---

## Important framing
This component is **not** allowed to invent trading logic. It is a thin, verifiable executor only.

Its only responsibilities are:
1. ensure the intended Tradovate window/module is active,
2. verify the visible instrument and context,
3. perform the requested action,
4. observe enough UI evidence to send an acknowledgement,
5. halt if confidence is low.

---

## External realities verified from current public sources
- Tradovate’s web platform supports trading-oriented modules including the **SuperDOM** and a **Trading** module.
- Tradovate documents an **Exit at Mkt & Cxl** control with a dropdown that includes **Cancel All**.
- Tradovate documents configurable **Hotkeys** in the web app.
- Tradovate’s web app is intended to be used in Chrome and other web browsers.

These points matter because the execution layer should prefer the most stable and testable action path available in the current UI.

---

## Recommended execution philosophy
Use this priority order:

### Mode A — Stable DOM locator clicks
Use Playwright locators for visible buttons/menu items when they can be resolved robustly.

### Mode B — Configured hotkeys
If the user configures unique Tradovate hotkeys for Buy, Sell, and Cancel All, hotkey execution can be more robust than brittle button paths.

### Mode C — Visual anchor + click fallback
Use only if locators and hotkeys both fail.

The implementation should support both Mode A and Mode B, with a configuration switch.

---

## Hard assumptions for v1
1. One dedicated Tradovate browser window/profile is reserved for automation.
2. One dedicated instrument is active for trading.
3. User keeps a consistent workspace layout.
4. User does not manually click inside the same module while automation is active.
5. Browser zoom remains fixed at 100%.
6. The selected quantity in the UI is already correct for the session, or quantity is controlled by a stable input element that can be set safely before trading starts.
7. The execution layer will not manage multiple accounts in v1.

---

## Architecture

```text
execution-layer/
  src/
    app.ts
    config/
      schema.ts
    browser/
      launch.ts
      workspace-guard.ts
    tradovate/
      module-detector.ts
      instrument-verifier.ts
      position-verifier.ts
      command-runner.ts
      cancel-all-runner.ts
      buy-runner.ts
      sell-runner.ts
      hotkey-runner.ts
    acks/
      ack-detector.ts
      timeout-manager.ts
    safety/
      action-lock.ts
      emergency-halt.ts
    bus/
      intent-client.ts
      ack-publisher.ts
    logs/
      audit-log.ts
```

---

## Input contract from signal engine

```ts
export type ExecutionIntent = {
  intentId: string;
  tsLocal: string;
  action: "BUY" | "SELL" | "CANCEL_ALL";
  reason: string;
  qty: number;
  expectedPrice?: number;
  positionEffect: "open" | "close" | "reduce" | "flat_cleanup";
  strategyStateSnapshot: Record<string, unknown>;
};
```

---

## Output acknowledgement contract

```ts
export type ExecutionAck = {
  intentId: string;
  ok: boolean;
  action: "BUY" | "SELL" | "CANCEL_ALL";
  tsLocal: string;
  message?: string;
  observedInstrument?: string;
  observedPositionAfter?: "flat" | "long" | "short" | "unknown";
  methodUsed?: "locator-click" | "hotkey" | "visual-fallback";
};
```

---

## Config schema

```json
{
  "browser": {
    "headless": false,
    "storageStatePath": "./storage/auth-state.json",
    "viewport": { "width": 1600, "height": 1000 }
  },
  "tradovate": {
    "baseUrl": "https://trader.tradovate.com",
    "expectedInstrumentRoot": "MNQ",
    "preferredModule": "superdom",
    "executionMode": "locator-click",
    "allowHotkeys": true,
    "actionAckTimeoutMs": 3000,
    "postActionSettleMs": 300,
    "preActionFocusClick": true
  },
  "selectors": {
    "instrumentAnchors": ["text=/MNQ/i"],
    "buyButtons": ["role=button[name=/buy/i]", "text=/buy/i"],
    "sellButtons": ["role=button[name=/sell/i]", "text=/sell/i"],
    "exitAtMktOrCxl": ["text=/exit at mkt/i", "text=/cxl/i"],
    "cancelAllItems": ["text=/cancel all/i"]
  },
  "hotkeys": {
    "buy": "Alt+B",
    "sell": "Alt+S",
    "cancelAll": "Alt+C"
  },
  "safety": {
    "requireInstrumentVerification": true,
    "requireVisibleModule": true,
    "blockIfPageHidden": true,
    "singleFlightActions": true,
    "maxFailedActions": 1
  }
}
```

---

## Workspace preparation requirements
Before runtime, require the user to set up a stable Tradovate workspace.

### Mandatory manual preparation
1. Open only the module used for automation.
2. Keep the traded instrument visible.
3. Ensure the account/instrument context is correct.
4. Set quantity before the session starts, or provide a stable quantity selector for automation.
5. If hotkeys will be used, assign **unique and non-conflicting** hotkeys in Tradovate settings.
6. Save workspace layout.

### Strong recommendation
Use a dedicated browser profile only for this bot. Do not browse other tabs in that same automated window.

---

## Runtime safety gates
Every action must pass these checks before any click/keypress is sent.

### Gate 1 — Page health
- page loaded
- no modal covering the UI
- no obvious disconnected/session-expired state

### Gate 2 — Instrument verification
- visible instrument must contain expected root symbol, e.g. `MNQ`
- if mismatch, reject action and halt

### Gate 3 — Module verification
- expected module visible and focused
- action controls present

### Gate 4 — Action lock
- only one action in flight at a time
- reject concurrent intents

### Gate 5 — Intent freshness
- if intent is older than configured staleness threshold, reject it

---

## Action implementations

## 1. BUY
### Goal
Open a long position if flat, or close a short if already short.

### Preferred method: locator click
1. Verify workspace gates.
2. Focus the Tradovate page and target module.
3. Resolve buy button locator from configured candidates.
4. Confirm the locator is visible and enabled.
5. Click once.
6. Wait `postActionSettleMs`.
7. Run acknowledgement detection.

### Alternate method: hotkey
1. Verify workspace gates.
2. Focus page.
3. Send configured buy hotkey.
4. Wait `postActionSettleMs`.
5. Run acknowledgement detection.

---

## 2. SELL
### Goal
Open a short position if flat, or close a long if already long.

Implementation mirrors BUY.

---

## 3. CANCEL_ALL
### Goal
Cancel all working orders for the selected instrument before entry or during safety cleanup.

### Preferred method
1. Verify workspace gates.
2. Resolve the visible control path that exposes the cancellation menu.
3. Open the control or dropdown.
4. Click **Cancel All**.
5. Wait for visual confirmation or disappearance of working-order markers if such indicators are available.
6. Emit acknowledgement.

If there are no visible working orders, the layer may still acknowledge success if the menu action was successfully triggered and no error is shown.

---

## Acknowledgement detection
Since there is no broker API in this workflow, acks must be based on UI evidence.

### Minimum acceptable ack sources
Any one of these may be used, in order of preference:
1. visible position/state change in the module
2. visible order/position row change in a stable panel
3. visible toast/notification confirming action
4. successful button/hotkey action plus a state re-check indicating no contradiction

### Ack for BUY
Prefer one of:
- visible position becomes long
- visible flat short becomes flat or long after action

### Ack for SELL
Prefer one of:
- visible position becomes short
- visible flat long becomes flat or short after action

### Ack for CANCEL_ALL
Prefer one of:
- working-order indicators disappear
- order row count drops
- a visible success notification appears

### If ack is missing
If no ack arrives within `actionAckTimeoutMs`:
- return `ok=false`
- halt the system
- do not retry automatically unless the user explicitly enables retries

---

## Why retries are dangerous
A missing ack could mean:
- the click failed,
- the click succeeded but UI confirmation is slow,
- the click executed against the wrong module,
- the position changed but the verifier missed it.

Automatic retry can therefore duplicate trades. For v1, default policy should be:
- **no blind automatic retries for BUY/SELL**
- at most one retry for `CANCEL_ALL` if the UI clearly shows the menu did not open

---

## Position verifier
A lightweight UI verifier should estimate only these states:
- `flat`
- `long`
- `short`
- `unknown`

Do **not** try to parse full account state in v1.

### Strategies for verifying position
Use any stable visible source, configurable at runtime:
- position badge or header
- SuperDOM position display
- Trading module order/position field
- PnL/position table row if stable

This verifier is not the source of truth for trade logic, but it is critical for action acknowledgements.

---

## Instrument verifier
Must run before every action.

### Rule
If the visible instrument does not match expected root `MNQ`, reject the action.

### Matching rules
- normalize case
- ignore spacing differences
- allow front-month variations such as `MNQM6`, `MNQU6`, etc.
- reject if another product root is visible as active instrument

---

## Action lock
Implement single-flight action execution.

```ts
class ActionLock {
  private busy = false;

  async runExclusive<T>(fn: () => Promise<T>): Promise<T> {
    if (this.busy) throw new Error("action already in flight");
    this.busy = true;
    try {
      return await fn();
    } finally {
      this.busy = false;
    }
  }
}
```

This prevents overlapping commands such as:
- `CANCEL_ALL` and `SELL` at the same time,
- duplicate BUY intents,
- rapid action storms during UI lag.

---

## Playwright interaction guidance
Playwright’s locator model is the preferred way to automate UI actions because locators are designed for retryability and auto-waiting.

### Best practices for this executor
- prefer role/text locators over raw CSS when possible
- scope locators to the visible trading module
- verify `visible` and `enabled` before action
- write screenshots on failure
- do not rely on absolute screen coordinates in primary mode

---

## Pseudocode
```ts
async function executeIntent(intent: ExecutionIntent): Promise<ExecutionAck> {
  return actionLock.runExclusive(async () => {
    await workspaceGuard.assertReady();
    await instrumentVerifier.assertExpected("MNQ");
    await moduleDetector.assertVisible();

    switch (intent.action) {
      case "BUY":
        await commandRunner.buy(intent);
        return await ackDetector.awaitBuyAck(intent);

      case "SELL":
        await commandRunner.sell(intent);
        return await ackDetector.awaitSellAck(intent);

      case "CANCEL_ALL":
        await commandRunner.cancelAll(intent);
        return await ackDetector.awaitCancelAllAck(intent);
    }
  });
}
```

---

## Failure modes and required behavior

### 1. Modal popup covers the page
Behavior:
- reject action
- screenshot
- return failed ack

### 2. Browser loses focus during hotkey mode
Behavior:
- reacquire focus
- if focus cannot be verified, fail closed

### 3. Button locator found in wrong panel
Behavior:
- require scoping to the active instrument/module container
- if ambiguous, reject action rather than guess

### 4. UI theme/layout change
Behavior:
- locator mode may still work if role/text remains stable
- visual fallback should be disabled until recalibrated

### 5. Session timeout / relogin page
Behavior:
- no action
- emit failed ack with `message=session_invalid`
- halt automation

---

## Logging requirements
Every action attempt must produce structured logs.

### Examples
```json
{"event":"intent_received","intentId":"...","action":"BUY"}
{"event":"gate_pass","name":"instrument_verification","instrument":"MNQM6"}
{"event":"action_sent","intentId":"...","action":"SELL","method":"locator-click"}
{"event":"ack_ok","intentId":"...","observedPositionAfter":"short"}
{"event":"ack_failed","intentId":"...","reason":"timeout"}
{"event":"halt","reason":"failed_action"}
```

Also save a screenshot for every failed ack.

---

## Recommended deployment path
### Phase 1 — dry executor
- connect to Tradovate simulation
- log all intended actions without clicking

### Phase 2 — cancel-only verification
- enable only `CANCEL_ALL`
- verify menu navigation and acks

### Phase 3 — single-action manual supervision
- enable BUY/SELL in simulation with user watching
- one action at a time

### Phase 4 — supervised automation
- connect signal engine and run in simulation
- verify ack consistency and state sync

### Phase 5 — limited live use
- only after repeated successful simulation sessions

---

## Acceptance criteria
- every intent produces exactly one ack
- no overlapping actions occur
- wrong-instrument protection blocks actions correctly
- failed ack halts the system
- cancel-all path works without requiring full page reload
- executor can run a full simulation session without desynchronizing internal state

---

## Engineering guidance to the coding LLM
- Keep the executor intentionally small.
- Put all Tradovate-specific selectors and hotkeys in config.
- Separate:
  - gating,
  - action sending,
  - ack detection,
  - failure handling.
- Make screenshots and structured logs first-class outputs.
- Prefer safety over liveness: reject ambiguous actions.

---

## Suggested first milestone
1. Launch Tradovate with saved session.
2. Detect active instrument/module.
3. Verify `MNQ` instrument.
4. Implement `CANCEL_ALL`.
5. Add acknowledgement.
6. Implement BUY and SELL with simulation only.
7. Add signal-engine integration.

---

## Source notes used for this design
- Tradovate public help pages for web trading modules, Cancel All path, and configurable hotkeys.
- Playwright official docs for robust browser automation patterns.
