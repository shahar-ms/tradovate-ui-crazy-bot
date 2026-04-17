"""
Theme + color tokens for the operator UI. Intentionally boring.

Color rules (from spec):
  - green  => clearly safe / ok
  - yellow => degraded / needs attention
  - red    => halted / unsafe / blocked
  - neutral slate background

Keep this list short. Don't sprinkle colors elsewhere — reference from here.
"""

from __future__ import annotations


# --- colors --- #

BG = "#12151b"
PANEL = "#1b2028"
PANEL_ALT = "#232a35"
BORDER = "#2f3947"
TEXT = "#e4e9f0"
TEXT_MUTED = "#8d97a5"

# Status (used by badges and state pills)
OK_GREEN = "#35c46a"
DEGRADED_YELLOW = "#d4a017"
BROKEN_RED = "#e04242"
INACTIVE_GRAY = "#5a6371"

# Action buttons
ARM_ORANGE = "#e8781e"      # arming is risky, stands out
HALT_RED = "#d42e2e"        # emergency
CANCEL_YELLOW = "#d4a017"   # cancel-all
PRIMARY_BLUE = "#3b82f6"    # normal safe actions

# --- stylesheet --- #

STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 12px;
}}
QMainWindow, QDialog {{ background-color: {BG}; }}

QFrame#panel {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QFrame#panelAlt {{
    background-color: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QLabel[role="title"] {{
    font-size: 13px;
    font-weight: 600;
    color: {TEXT};
    padding: 2px 0;
}}
QLabel[role="muted"]     {{ color: {TEXT_MUTED}; }}
QLabel[role="big"]       {{ font-size: 22px; font-weight: 600; }}
QLabel[role="bigger"]    {{ font-size: 28px; font-weight: 700; }}

QLabel[status="ok"]         {{ color: {OK_GREEN};        font-weight: 600; }}
QLabel[status="degraded"]   {{ color: {DEGRADED_YELLOW}; font-weight: 600; }}
QLabel[status="broken"]     {{ color: {BROKEN_RED};      font-weight: 600; }}
QLabel[status="inactive"]   {{ color: {INACTIVE_GRAY};   font-weight: 600; }}

QPushButton {{
    background-color: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 12px;
    color: {TEXT};
}}
QPushButton:hover     {{ background-color: #2c3541; }}
QPushButton:disabled  {{ color: {TEXT_MUTED}; background-color: #1a1e25; }}

QPushButton[role="primary"]  {{ background-color: {PRIMARY_BLUE}; border: none; font-weight: 600; }}
QPushButton[role="arm"]      {{ background-color: {ARM_ORANGE};   border: none; font-weight: 700; color: #101010; }}
QPushButton[role="halt"]     {{ background-color: {HALT_RED};     border: none; font-weight: 700; font-size: 14px; }}
QPushButton[role="cancel"]   {{ background-color: {CANCEL_YELLOW};border: none; font-weight: 700; color: #101010; }}
QPushButton[role="danger"]   {{ background-color: #7a1414;        border: 1px solid {HALT_RED}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {PANEL_ALT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 6px;
    color: {TEXT};
    selection-background-color: {PRIMARY_BLUE};
}}
QCheckBox {{ spacing: 6px; }}

QTableWidget, QListWidget, QTreeWidget {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    gridline-color: {BORDER};
    alternate-background-color: {PANEL_ALT};
}}
QHeaderView::section {{
    background-color: {PANEL_ALT};
    color: {TEXT_MUTED};
    border: none;
    padding: 4px 6px;
    font-weight: 600;
}}

QTabBar::tab {{
    background: {PANEL};
    border: 1px solid {BORDER};
    padding: 6px 12px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {PANEL_ALT}; }}
QTabWidget::pane      {{ border: 1px solid {BORDER}; top: -1px; }}

QListWidget[role="nav"]::item {{
    padding: 10px 14px;
    border-bottom: 1px solid #191d23;
}}
QListWidget[role="nav"]::item:selected {{
    background: {PRIMARY_BLUE};
    color: #ffffff;
}}
"""


def status_color(state: str) -> str:
    return {
        "ok": OK_GREEN,
        "degraded": DEGRADED_YELLOW,
        "broken": BROKEN_RED,
        "inactive": INACTIVE_GRAY,
    }.get(state, INACTIVE_GRAY)
