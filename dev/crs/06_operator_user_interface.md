# 06 — Operator User Interface Specification

## Why this file exists
This file tells the coding LLM how to build the **human-facing desktop application** for the bot.

The previous files define the bot internals:
- file 01: foundation and calibration
- file 02: price stream reader
- file 03: execution layer
- file 04: signal engine
- file 05: orchestrator and runbook

This file defines the **operator UI** that sits on top of those parts.

The UI is not the trading strategy.
The UI is not the OCR engine.
The UI is not the executor.

The UI is the safe control panel that lets a human:
- configure the bot
- calibrate the screen points and regions
- monitor health
- see the current price and state
- see intended actions
- arm/disarm trading
- halt the bot instantly
- trigger manual cancel-all
- review logs and recent screenshots

This file should help the coding LLM build a usable interface without needing to invent the UX.

---

## Design goal
Build a **small desktop control application** for one operator running one Tradovate session on one machine.

The UI must feel like a local operations console, not like a polished SaaS product.

Priorities, in order:
1. clarity
2. safety
3. fast calibration
4. obvious runtime status
5. low complexity

Avoid fancy design.
Avoid browser-based frontends.
Avoid remote multi-user complexity.
Avoid custom rendering unless needed.

---

## Recommended tech stack
Use **Python + PySide6**.

Why:
- It is a good fit for a desktop control application with panels, dialogs, forms, tables, tabs, and status indicators.
- Qt widgets are a natural match for control panels and tools, and the framework is built around signals and slots for communication between UI and worker objects. citeturn506032search2turn506032search0turn506032search13
- PySide6 supports thread-safe cross-thread communication through queued signal-slot connections, which matches this bot architecture where background workers produce price updates, health updates, and execution acknowledgements. citeturn506032search5turn506032search4
- Qt’s model/view approach is suitable for logs, recent events, and table-style state displays if the UI grows beyond a very basic panel. citeturn506032search3turn506032search12

Do not use Electron.
Do not use React for v1.
Do not use a web dashboard for localhost unless there is a very strong reason later.

---

## UI product definition
The application should be a **single-window desktop app** with the following main sections:

1. top status bar
2. left-side navigation or tab strip
3. main content area
4. bottom emergency action strip

The operator should be able to run the full bot from this one window.

---

## Required screens
Implement these screens.

### 1. Dashboard
Purpose:
- show current runtime state at a glance
- show whether the bot is safe
- show current price stream health
- show current strategy state
- show current execution state
- expose main control buttons

This is the default screen when the app starts.

### 2. Calibration screen
Purpose:
- let the user define all required points and regions on the Tradovate screen
- verify saved calibration visually
- re-run calibration at any time

This screen is mandatory because your whole architecture depends on user-marked screen locations.

### 3. Strategy settings screen
Purpose:
- configure signal parameters
- configure TP/SL and risk controls
- configure timing rules and safety thresholds

### 4. Execution settings screen
Purpose:
- configure action delays, click behavior, hotkey fallback, guard thresholds
- choose test mode vs armed mode behavior

### 5. Logs and events screen
Purpose:
- let the operator inspect recent actions, recent price parse failures, halts, acknowledgements, and screenshots

### 6. Run control screen
Purpose:
- explicit mode switching
- arm/disarm flow
- halt/reset flow
- manual cancel-all

These can be separate tabs or grouped views in a side navigation layout.

---

## Main window layout
Recommended structure:

```text
+--------------------------------------------------------------------------------+
| App title | Session ID | Mode badge | Price health | Exec health | Anchor guard |
+----------------------+---------------------------------------------------------+
| Navigation           | Main content area                                       |
| - Dashboard          |                                                         |
| - Calibration        |                                                         |
| - Strategy           |                                                         |
| - Execution          |                                                         |
| - Logs               |                                                         |
| - Run Control        |                                                         |
+----------------------+---------------------------------------------------------+
| [Pause] [Disarm] [Cancel All] [Halt]                last update ts | version   |
+--------------------------------------------------------------------------------+
```

Keep the layout boring and predictable.

---

## Visual principles
Use these visual rules:

- green only means clearly safe/ok
- yellow means degraded/needs attention
- red means halted/unsafe/action blocked
- do not overload the UI with too many colors
- use large readable labels for runtime mode
- surface the halt reason visibly at all times when halted
- keep action buttons large and hard to misclick
- dangerous buttons must be visually distinct

Do not hide critical state in tiny text.

---

## Screen-by-screen requirements

# Dashboard

## Dashboard purpose
This page should answer these questions immediately:
- is the bot connected to a valid screen layout?
- is OCR healthy?
- is the bot flat or in a position?
- what was the last intended action?
- what was the last execution acknowledgement?
- is it armed or not?
- if halted, why?

## Dashboard widgets
Implement the following widgets:

### Runtime summary card
Fields:
- current mode
- armed yes/no
- halted yes/no
- halt reason
- session id
- uptime

### Market snapshot card
Fields:
- last accepted price
- last price timestamp
- OCR confidence
- accepted ticks count
- rejected ticks count
- price stream health

### Strategy state card
Fields:
- position state: flat / pending / long / short
- current detected level(s)
- cooldown active yes/no
- daily loss lock yes/no
- last signal type
- current stop and target if position exists

### Execution state card
Fields:
- execution health
- last ack result
- last action timestamp
- click driver mode
- hotkey fallback enabled yes/no

### Anchor and screen guard card
Fields:
- anchor guard status
- current monitor id
- current resolution
- calibration loaded yes/no
- last anchor check timestamp

### Recent events panel
Show recent events in reverse order, for example:
- price parse rejected
- entry intent emitted
- buy ack ok
- halt triggered
- cancel-all executed

Use a simple table or list.

### Main action buttons on dashboard
Show these buttons prominently:
- `Start Price Debug`
- `Start Paper Mode`
- `Arm Live Trading`
- `Disarm`
- `Cancel All`
- `Halt`

Rules:
- if not calibrated, live controls disabled
- if health degraded, arm button disabled
- if halted, show `Reset Halt` only after operator acknowledges halt reason

---

# Calibration screen

## Calibration purpose
The calibration screen is the most important screen for initial setup.

It must allow the operator to define these regions/points on the Tradovate screen:
- price read region
- instrument anchor region
- buy point
- sell point
- cancel-all point
- optional position status region
- optional toast/ack region

## Calibration workflow
The operator flow should be explicit and guided.

Recommended steps:
1. choose monitor
2. capture current screen preview
3. mark price region
4. mark anchor region
5. mark buy point
6. mark sell point
7. mark cancel-all point
8. mark optional ack region
9. save calibration
10. run validation checks

## Required controls
Implement:
- screen preview panel
- zoomed crop preview for currently selected region
- button to start point marking
- button to start rectangle marking
- list of saved calibration items
- save button
- load button
- reset current item button
- full reset button
- validation button

## Calibration interaction design
For rectangles:
- click and drag to draw
- show x, y, width, height
- allow nudging by arrow keys or small step buttons

For points:
- single click to place point
- show x and y
- allow nudging by arrow keys or small step buttons

## Validation rules
Add a `Validate Calibration` button.
It should verify:
- all required items exist
- items are within selected monitor bounds
- price region screenshot can be captured
- anchor region screenshot can be captured
- click points are not outside screen

Optional extra validation:
- show live OCR preview from the price region
- show anchor similarity score vs saved reference

## Calibration output
Save to a human-readable JSON file.

Example logical structure:

```json
{
  "monitor_id": 1,
  "resolution": [1920, 1080],
  "price_region": {"x": 120, "y": 140, "w": 180, "h": 44},
  "anchor_region": {"x": 80, "y": 60, "w": 220, "h": 40},
  "buy_point": {"x": 1650, "y": 900},
  "sell_point": {"x": 1710, "y": 900},
  "cancel_all_point": {"x": 1770, "y": 900},
  "ack_region": {"x": 1400, "y": 130, "w": 280, "h": 80}
}
```

The UI should not assume fixed field names beyond the internal schema the bot uses.

---

# Strategy settings screen

## Purpose
This screen edits the parameters used by file 04.

The UI must expose settings without requiring manual JSON editing.

## Groups to show

### Entry model
Fields:
- strategy enabled
- long entries enabled
- short entries enabled
- sweep return threshold
- confirmation ticks
- minimum move filter
- debounce window

### Stop and target
Fields:
- stop loss points
- take profit points
- optional break-even enable
- break-even trigger
- optional trailing enable
- trailing distance

### Time controls
Fields:
- allowed session start
- allowed session end
- max trade duration
- cooldown after exit
- cooldown after loss

### Safety rules
Fields:
- max entries per session
- max consecutive losses
- max daily loss
- halt on unknown ack yes/no
- halt on degraded price stream yes/no

### Replay/testing
Fields:
- test mode only yes/no
- replay speed
- mock execution yes/no

## UX rules
- each field must have a short explanation text
- numeric fields need min/max validation
- unsaved changes should be obvious
- provide `Save`, `Revert`, and `Load Defaults`

---

# Execution settings screen

## Purpose
This screen edits the behavior of file 03.

## Groups to show

### Click behavior
Fields:
- pre-click move duration
- post-click delay
- double-click mode yes/no
- confirmation wait timeout
- retry cancel-all yes/no
- max cancel-all retries

### Guard behavior
Fields:
- anchor similarity threshold
- block execution if anchor fails yes/no
- block execution if wrong resolution yes/no
- stale screen timeout

### Hotkey fallback
Fields:
- enable hotkeys yes/no
- buy hotkey
- sell hotkey
- cancel-all hotkey

### Test mode
Fields:
- draw click overlays yes/no
- log screenshots on action yes/no
- ask confirmation before live click yes/no

## UX rules
- risky fields should show warnings
- any live-impacting settings change should require save confirmation

---

# Logs and events screen

## Purpose
This screen is for diagnosis and trust.

The operator must be able to see what the bot believed, what it tried to do, and what happened.

## Required tabs or sections

### Event log
Columns:
- timestamp
- event type
- severity
- short message

### Price parse log
Columns:
- timestamp
- raw OCR text
- parsed value
- confidence
- accepted/rejected
- reject reason

### Execution log
Columns:
- timestamp
- requested action
- result
- ack state
- notes

### Halt log
Columns:
- timestamp
- halt reason
- component
- cleared by

### Screenshots
Show recent saved screenshots or cropped debug images.
For v1, a file list and image preview is enough.

## Interaction rules
- support filtering by severity and type
- support copy-to-clipboard for selected row
- support open log folder
- support export current filtered view to CSV later, but not mandatory for v1

---

# Run control screen

## Purpose
This is the explicit operational control page.

Think of it as the page where a human deliberately chooses bot mode.

## Required controls
Buttons:
- `Enter Calibration Mode`
- `Start Price Debug`
- `Start Paper Mode`
- `Arm Live Trading`
- `Disarm`
- `Cancel All`
- `Halt Now`
- `Reset Halt`
- `Shutdown Bot`

## State gating rules
The UI must enforce these rules:
- cannot arm if calibration missing
- cannot arm if price stream unhealthy
- cannot arm if anchor guard failing
- cannot arm if halt state active
- cannot arm if execution test has never passed

When blocking arm, explain exactly why.
Do not just disable the button silently.

## Arming flow
For live trading, require a two-step confirmation dialog:
1. show current checks and risk summary
2. operator confirms intentionally

Suggested confirmation text:
- current mode
- current price stream health
- anchor guard status
- execution test last pass time
- strategy name
- stop loss / take profit config
- max daily loss

Only then allow `ARMED` mode.

---

## Bottom emergency strip
Always visible at the bottom of the window.

Buttons:
- `Disarm`
- `Cancel All`
- `Halt`

Rules:
- `Halt` must be large and red
- `Cancel All` must be large and very visible
- actions must not depend on which page is open

This strip is important because the operator must always have emergency access.

---

## Suggested internal UI architecture
Create a dedicated UI package, for example:

```text
app/ui/
  main_window.py
  app_signals.py
  theme.py
  widgets/
    status_badge.py
    labeled_value.py
    event_table.py
    image_preview.py
    calibration_canvas.py
    confirm_dialog.py
  pages/
    dashboard_page.py
    calibration_page.py
    strategy_page.py
    execution_page.py
    logs_page.py
    run_control_page.py
  dialogs/
    arm_confirm_dialog.py
    halt_reason_dialog.py
    calibration_help_dialog.py
```

Keep the pages modular.
Do not put the whole UI in one giant file.

---

## Communication pattern between bot and UI
Use signals and slots.

Recommended pattern:
- worker threads produce typed updates
- UI receives them through Qt signals
- UI never blocks worker threads
- UI never performs long-running bot work itself

Examples of signals:
- `price_updated(price_tick)`
- `health_updated(component_health)`
- `event_logged(event_row)`
- `execution_ack_received(ack)`
- `mode_changed(runtime_state)`
- `halt_triggered(reason)`

This should map cleanly to Qt’s intended event-driven communication model. citeturn506032search0turn506032search5

---

## Recommended UI state model
Maintain a small front-end state object inside the app.

Example fields:
- current mode
- last price
- last confidence
- price health
- execution health
- anchor guard status
- armed flag
- halted flag
- halt reason
- current position
- last intent
- last ack
- config dirty flags by page

The UI should not query random subsystems directly on every paint.
Use pushed updates and store the latest values.

---

## Error handling rules
The UI must handle these gracefully:
- config file missing
- calibration file missing
- OCR worker not started
- execution worker unavailable
- runtime queue disconnected
- screenshot preview failure
- invalid numeric input

Rules:
- show readable error messages
- never crash the whole UI because one panel failed to update
- keep the emergency buttons available whenever possible

---

## First usable version definition
The first UI release is good enough when it can do all of this:
- load existing config
- let user calibrate all required regions/points
- show live last price and OCR confidence
- show current runtime mode
- show last signal and last ack
- arm and disarm the bot
- halt the bot
- manual cancel-all
- show recent logs and recent screenshots

Anything beyond that is optional for v1.

---

## Nice-to-have, but not required in v1
Do not block v1 on these:
- dark theme polishing
- custom charts
- drag-and-drop screenshot manager
- multiple saved layouts
- multi-monitor live minimap
- remote control over network
- user authentication
- beautiful animations

Keep v1 plain.

---

## Suggested implementation order for the UI itself
Build the UI in this order:

1. app shell and main window
2. top status bar and bottom emergency strip
3. dashboard page with mock data
4. calibration page with save/load JSON
5. run control page with mock commands
6. real integration to runtime state updates
7. strategy settings page
8. execution settings page
9. logs and screenshots page
10. arm confirmation and halt dialogs

Reason:
- the coder will see the UI early
- the runtime can be integrated gradually
- the calibration page becomes usable quickly

---

## Testing checklist for the UI coder
Before calling the UI done, verify:

### Basic app
- main window opens
- pages switch correctly
- no crashes on startup without live workers

### Calibration
- all required points/regions can be marked
- JSON saves and reloads correctly
- monitor bounds validation works
- preview updates correctly

### Dashboard
- mock state renders correctly
- live state updates do not freeze the UI
- halt reason becomes visible when present

### Run control
- buttons enable/disable correctly according to guards
- arm flow requires explicit confirmation
- halt always works
- cancel-all always accessible

### Logs
- event rows append correctly
- filters work
- screenshot preview opens

### Safety
- UI does not freeze during background updates
- invalid input is rejected clearly
- emergency strip remains accessible

---

## Final instruction to the coding LLM
Build this UI as a **tool for one human operator**.
It should feel like a local control console for a sensitive automation system.

Prefer:
- readable widgets
- strong safety signals
- simple forms
- obvious state
- boring reliability

Do not over-engineer visuals.
Do not add features that hide risk.
Do not make the operator guess what the bot is doing.

The UI succeeds if a tired human can open it and immediately answer:
- what mode am I in?
- is the bot healthy?
- what price is it reading?
- what does it want to do?
- can I stop it right now?
