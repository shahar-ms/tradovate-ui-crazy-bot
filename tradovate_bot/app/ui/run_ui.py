"""
Entry point for the operator UI.

    python -m app.ui.run_ui

The app is a single always-on-top floating panel. Calibration opens as a
modal dialog on first run or via the Setup button. No main window, no tray
icon, no navigation.
"""

from __future__ import annotations

import sys

from app.ui.hud_app import boot


def main(argv: list[str] | None = None) -> int:
    return boot(argv)


if __name__ == "__main__":
    sys.exit(main())
