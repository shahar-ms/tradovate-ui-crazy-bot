# Tradovate Web UI Price Stream Extraction Spec

## Purpose
Build a **browser-automation-compatible price stream** for the Tradovate web application, without using the Tradovate API. The output of this component is a normalized tick stream that downstream strategy logic can consume in real time.

This document is written for a coding LLM or developer implementing the component.

---

## Important framing
This component is for **experimental automation only**. It does **not** create any expectation of profit, does **not** guarantee reliability under changing UI conditions, and should be validated in simulation before it is connected to live order execution.

---

## Real-world constraints verified from current public sources
- Tradovate’s web platform exposes trading-oriented modules including **SuperDOM** and **Trading** modules on the web application.
- Tradovate requires a **funded account** for live market data.
- Tradovate’s official API access currently requires a **live funded account with at least $1,000**.
- Tradovate’s pricing page currently advertises **free simulated trading** and low intraday micro margins.

These facts matter because the design must work **without API access** and should be testable in simulation first.

---

## Assumptions for v1
If any of these are false, adjust the implementation.

1. OS: Windows 10/11.
2. Runtime: Node.js 20+ with TypeScript.
3. Browser automation: Playwright using bundled Chromium.
4. User manually logs into Tradovate once and persists session state.
5. User trades a **single instrument at a time** in a dedicated Tradovate browser profile/window.
6. The monitored instrument is visible in either:
   - SuperDOM, or
   - Trading module / quote area, or
   - a chart header area that displays the current last price.
7. UI layout is kept stable during the trading session.
8. For v1, the monitored instrument is **MNQ** only.

---

## Design goals
1. **Low latency**: emit new price events as fast as the UI visibly updates.
2. **Deterministic**: same page state should produce same parsed output.
3. **DOM-first**: prefer browser DOM extraction over OCR.
4. **Resilient**: survive minor CSS/class-name changes.
5. **Observable**: log source mode, parse confidence, update frequency, and heartbeat.
6. **Safe degradation**: if price cannot be trusted, emit `stream_health=degraded` and stop trading upstream.

---

## Non-goals
- No order placement here.
- No strategy logic here.
- No dependence on private/internal websocket protocol decoding.
- No attempt to reverse-engineer hidden Tradovate network payloads in v1.
- No OCR as the primary method.

---

## Recommended architecture

```text
tradovate-ui-reader/
  src/
    app.ts
    config/
      schema.ts
    browser/
      launch.ts
      auth.ts
      page-bootstrap.ts
    discovery/
      price-target-discovery.ts
      dom-signature.ts
    readers/
      dom-price-reader.ts
      visual-fallback-reader.ts
      health-monitor.ts
    parsers/
      price-parser.ts
      instrument-parser.ts
    streams/
      tick-bus.ts
      ring-buffer.ts
    models/
      tick.ts
      stream-health.ts
    persistence/
      state-store.ts
      logs.ts
    utils/
      time.ts
      retry.ts
      debounce.ts
  storage/
    auth-state.json
    screenshots/
    logs/
  config/
    bot.config.json
```

---

## Source-of-truth extraction priority
The extraction pipeline should use the following priority order:

### Mode A — DOM text extraction (preferred)
Use Playwright locators to find the visible element representing the current tradable price.

### Mode B — DOM mutation subscription (preferred once target found)
Once a valid price node is found, attach a `MutationObserver` inside the page to capture updates immediately.

### Mode C — Visual fallback in bounded region
If the DOM target becomes unstable, crop a small screen region around the known price area and apply a visual parse fallback. This should be a **fallback only**, not the default path.

### Mode D — Fail closed
If none of the above work reliably, mark the stream unhealthy and block trading decisions.

---

## Why this design
Playwright’s locator system is specifically designed for resilient web automation, with auto-waiting and retry behavior. `MutationObserver` is appropriate for DOM change detection. Therefore the implementation should prefer **locator-based discovery + in-page mutation listening** over polling the full page or using raw pixel OCR as the primary method.

---

## Data contract
The output of this module must be a normalized event stream.

```ts
export type PriceTick = {
  tsExchangeLike?: string | null;    // optional, if UI exposes it
  tsLocal: string;                   // ISO timestamp created locally
  instrument: string;                // e.g. "MNQM6"
  rootSymbol: string;                // e.g. "MNQ"
  price: number;                     // e.g. 21432.75
  sourceMode: "dom-text" | "dom-mutation" | "visual-fallback";
  confidence: number;                // 0.0..1.0
  sequence: number;                  // local monotonic sequence
  rawText?: string;
};
```

### Health contract
```ts
export type StreamHealth = {
  status: "healthy" | "degraded" | "stalled" | "broken";
  reason?: string;
  lastTickTs?: string;
  ticksPerSecond?: number;
  sourceMode?: "dom-text" | "dom-mutation" | "visual-fallback";
  consecutiveParseFailures: number;
};
```

---

## Config file

```json
{
  "browser": {
    "headless": false,
    "storageStatePath": "./storage/auth-state.json",
    "viewport": { "width": 1600, "height": 1000 },
    "slowMoMs": 0
  },
  "tradovate": {
    "baseUrl": "https://trader.tradovate.com",
    "instrumentRoot": "MNQ",
    "preferredModule": "superdom",
    "layoutStabilizationMs": 3000
  },
  "reader": {
    "scanIntervalMs": 250,
    "heartbeatWarnMs": 1500,
    "heartbeatBrokenMs": 4000,
    "maxAllowedJumpPoints": 50,
    "minConfidenceToEmit": 0.8,
    "enableVisualFallback": true,
    "visualFallbackRegionPaddingPx": 40
  },
  "logging": {
    "writeScreenshotsOnFailure": true,
    "writeStructuredLogs": true
  }
}
```

---

## Page bootstrap sequence
1. Launch Chromium with Playwright.
2. Load saved authenticated session state.
3. Navigate to Tradovate web application.
4. Wait for page idle and trading UI visibility.
5. Ensure only the instrument intended for trading is visible in the monitored module.
6. Normalize zoom level and window size.
7. Open the preferred module:
   - SuperDOM if available,
   - otherwise the Trading module or quote area.
8. Freeze layout assumptions for the session:
   - do not move modules,
   - do not switch themes during runtime,
   - do not resize the browser manually.

---

## Target discovery strategy
Do **not** hard-code fragile CSS class names as the only method.

Instead use a **multi-pass discovery approach**.

### Pass 1 — Instrument anchoring
Find visible text near the module that matches the intended instrument root, such as `MNQ` or a current contract string containing `MNQ`.

### Pass 2 — Nearby numeric candidates
Inside the anchored module, collect visible text nodes matching futures price format:
- numeric
- decimal
- multiple of the product tick size
- frequent updates

For MNQ, valid prices should align to **0.25 point increments**.

### Pass 3 — Candidate scoring
Score each candidate using:
- proximity to instrument anchor
- update frequency
- numeric validity
- persistence across rescans
- whether the value changes while the market is active

### Pass 4 — Mutation binding
Once the best candidate is found, install an in-page `MutationObserver` tied to that specific node or its nearest stable container.

---

## Candidate scoring model

```ts
score =
  proximityScore * 0.30 +
  numericValidityScore * 0.20 +
  updateFrequencyScore * 0.20 +
  persistenceScore * 0.20 +
  visibilityScore * 0.10;
```

### Numeric validity rules for MNQ
- must parse as finite number
- must be within an expected human-reasonable range for current MNQ price
- fractional part must be one of: `.00`, `.25`, `.50`, `.75`
- reject countdown timers, quantities, PnL values, volume, ladder sizes

Do **not** hard-code an exact absolute MNQ range. Instead allow a wide sanity band configurable in config.

---

## In-page mutation observer
Inject a small script into the page once the target node is known.

Responsibilities:
1. Watch the node and optionally its text-bearing descendants.
2. On each mutation, re-read text content.
3. Parse and validate candidate price.
4. Push serialized events back to the Playwright host via `page.exposeBinding` or repeated `page.evaluate` bridge pattern.

### Pseudocode
```ts
// host side
await page.exposeBinding("onPriceMutation", async (_source, payload) => {
  tickBus.publish(payload);
});

await page.evaluate((selector) => {
  const target = document.querySelector(selector);
  if (!target) throw new Error("price target not found");

  const emit = () => {
    const rawText = target.textContent?.trim() ?? "";
    // parse in page or send raw text to host
    // @ts-ignore
    window.onPriceMutation({ rawText, tsLocal: new Date().toISOString() });
  };

  const observer = new MutationObserver(() => emit());
  observer.observe(target, {
    childList: true,
    subtree: true,
    characterData: true
  });

  emit();
}, stableSelector);
```

---

## Parsing pipeline
### Raw text normalization
- trim whitespace
- remove commas if present
- normalize unicode minus signs
- reject empty strings
- reject strings containing both price-looking text and obvious label text

### Price parse rules
```ts
function parseTradovatePrice(raw: string, tickSize: number): number | null {
  const m = raw.match(/-?\d+(?:\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  if (!Number.isFinite(n)) return null;
  const fractional = Math.round((n % 1) * 100) / 100;
  const ok = [0, 0.25, 0.5, 0.75].some(v => Math.abs(fractional - v) < 1e-6);
  return ok ? n : null;
}
```

### Emit de-duplicated ticks
- emit every valid **price change**
- optionally emit heartbeat duplicates every X ms if downstream strategy needs steady cadence
- assign monotonic `sequence`

---

## Stream quality controls
The reader must continuously validate itself.

### Heartbeat rules
- `healthy`: price changed or was confirmed recently
- `degraded`: no update for `heartbeatWarnMs`
- `stalled`: no update for `heartbeatBrokenMs`
- `broken`: repeated parse failures or target vanished

### Gap rules
If the parsed price jumps by more than `maxAllowedJumpPoints` in one UI update, flag the tick as suspicious unless a second confirmation arrives immediately after.

### Re-discovery rules
Trigger target re-discovery when:
- target element disappears
- target becomes hidden
- parse failure count exceeds threshold
- browser navigation occurs
- module re-renders and selector no longer resolves

---

## Visual fallback mode
Use only if DOM mode fails.

### Principle
Capture a **small clipped screenshot** around the known price area and run a very narrow recognition pipeline.

### Recommended fallback approach
1. Use Playwright to screenshot the clipped region.
2. Apply image preprocessing:
   - grayscale
   - threshold
   - optional scale up 2x
3. Restrict allowed characters to digits, decimal point, optional minus sign.
4. Re-validate against tick-size rules.
5. Require two consecutive matching parses before emitting in fallback mode.

### Fallback limitations
- theme changes can break recognition
- browser zoom can break templates
- anti-aliasing differs by machine
- latency is worse than DOM mode

Because of this, fallback mode should emit `confidence <= 0.75` and upstream strategy should either halt or reduce aggressiveness.

---

## Storage and logs
Write structured JSON logs for every state transition.

### Example log lines
```json
{"event":"target_discovered","mode":"dom-text","selector":"div[data-x='...']","confidence":0.93}
{"event":"tick","instrument":"MNQM6","price":21432.75,"sourceMode":"dom-mutation","confidence":0.98}
{"event":"stream_health","status":"degraded","reason":"no mutation for 1700ms"}
{"event":"rediscovery_started","reason":"selector_missing"}
```

Also store failure screenshots when:
- target discovery fails
- parse fails repeatedly
- stream transitions to `broken`

---

## Public interface to strategy engine
Prefer local IPC or websocket between processes.

### Option A — Local websocket
- price reader publishes JSON messages to `ws://127.0.0.1:PORT`
- strategy engine subscribes

### Option B — append-only local file or named pipe
Acceptable, but websocket is cleaner for real-time state.

### Websocket message example
```json
{
  "type": "price_tick",
  "payload": {
    "tsLocal": "2026-04-16T13:31:10.127Z",
    "instrument": "MNQM6",
    "rootSymbol": "MNQ",
    "price": 21432.75,
    "sourceMode": "dom-mutation",
    "confidence": 0.98,
    "sequence": 4182
  }
}
```

---

## Failure modes and required behavior

### 1. Tradovate UI re-renders
Behavior:
- mark `degraded`
- start target re-discovery
- block downstream trading until recovered

### 2. Browser is minimized or hidden
Behavior:
- mark `degraded`
- if visual fallback is active, disable it
- optionally keep DOM mode if still functional

### 3. Network freezes / market data pauses
Behavior:
- do not invent ticks
- emit health warnings
- require downstream engine to halt entries

### 4. Instrument accidentally changed
Behavior:
- detect mismatch between expected root `MNQ` and visible anchor
- immediately halt stream

### 5. Duplicate visible price widgets
Behavior:
- keep highest-scoring candidate
- write debug log containing top 5 candidates and scores

---

## Testing plan

### Unit tests
- parse valid price strings
- reject invalid numeric strings
- reject non-tick-aligned values
- deduplication logic
- confidence scoring logic

### Integration tests against static mock HTML
- multiple numeric nodes
- rerendered DOM containers
- delayed text appearance
- hidden target becoming visible

### Live dry-run tests in Tradovate simulation
1. Launch Tradovate simulated environment.
2. Open MNQ in one dedicated module.
3. Run reader for 30 minutes.
4. Record:
   - missed updates
   - false parses
   - rediscovery events
   - average latency from visible update to emitted tick

### Acceptance criteria for v1
- correct instrument recognized for the full session
- no more than 1 false price parse in 10,000 ticks
- recovery from a single module re-render without manual restart
- stream health transitions correctly during stalls
- visual fallback disabled automatically once DOM mode recovers

---

## Engineering notes for the coding LLM
- Prefer Playwright **locators** over brittle selectors whenever possible.
- Use `MutationObserver` for push-style DOM updates once the target is identified.
- Keep a strict separation between:
  - discovery,
  - reading,
  - validation,
  - publishing.
- Build the reader so that product-specific assumptions are configurable, especially tick size and instrument root.
- Keep the full implementation deterministic and heavily logged.

---

## Suggested first milestone
Deliver this sequence before anything else:
1. Launch browser with saved session.
2. Open Tradovate and locate the MNQ module.
3. Discover a valid current price node.
4. Emit parsed prices to console in real time.
5. Add stream health.
6. Add fallback and rediscovery.

Only after that should this component be connected to the signal engine.

---

## Source notes used for this design
- Tradovate public help/pricing material indicating web trading modules, market data requirements, simulation access, and API access threshold.
- Playwright official docs for locator-based automation and screenshots.
- MDN docs for `MutationObserver`.
