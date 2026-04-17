# Tradovate UI Automation Bot

Screen-automation trading bot for Tradovate web app. MNQ futures, RTH only.

## Status

Implemented in 5 waves. See `dev/crs/` for the spec per wave.

- [x] Wave 1 — Foundation + Calibration
- [ ] Wave 2 — Price Stream Reader
- [ ] Wave 3 — Execution Layer
- [ ] Wave 4 — Signal Engine
- [ ] Wave 5 — Orchestrator + Runbook

## Setup

```bash
cd tradovate_bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Tesseract must be installed separately (Windows installer) and its path set in `.env` as `TESSERACT_CMD`.

## Calibration

Run once per screen layout:

```bash
python -m app.calibration.calibrator
```

Follow the prompts: pick a monitor, then mark anchor region, price region, and the three click points (buy, sell, cancel-all).

Artifacts saved to:
- `app/config/screen_map.json`
- `runtime/screenshots/calibration_full.png`
- `runtime/screenshots/anchor_reference.png`
- `runtime/screenshots/calibration_overlay.png`

## Validation

```bash
python -m app.calibration.validator
```

Prints a pass/fail report and writes `READY_FOR_FILE_02` when all checks succeed.

## Tests

```bash
pytest tests/
```

## Safety

- Paper/dry-run modes are the default.
- Armed mode requires explicit opt-in.
- Anchor guard halts the bot if the screen layout drifts.
