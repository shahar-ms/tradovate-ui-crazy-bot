"""
Trade-management panel for the floating HUD.

Two visual modes (swapped via QStackedWidget so layout stays stable):
  - SLIM (~24 px): single muted "FLAT — awaiting entry" line. Shown when
    the bot is flat. Keeps the HUD compact while idle.
  - FULL: 2-column grid of side / size / entry / current / stop / target,
    with a separate big PnL banner below. Shown while in-position so each
    parameter sits on its own clearly-labeled element.

Reads everything it needs from a UiState snapshot — no Qt signals, no
threads. The HUD calls `apply_state(state)` from its existing refresh
tick.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QStackedWidget,
                               QVBoxLayout, QWidget)

from app.ui.theme import (BORDER, BROKEN_RED, INACTIVE_GRAY, OK_GREEN,
                          PANEL_ALT, TEXT, TEXT_MUTED)
from app.ui.ui_state import UiState


_LABEL_STYLE = f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 1px;"
_VALUE_STYLE = f"color: {TEXT}; font-size: 12px; font-weight: 600;"
_SLIM_STYLE = (
    f"color: {TEXT_MUTED}; font-size: 11px; letter-spacing: 2px; "
    "font-weight: 600;"
)


class TradePanel(QFrame):
    """In-trade UI block. Owns its own internal slim/full state machine."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("tradePanel")
        # Subtle bordered background so the panel reads as one grouped unit
        # against the HUD's flatter background.
        self.setStyleSheet(
            f"""
            QFrame#tradePanel {{
                background-color: {PANEL_ALT};
                border: 1px solid {BORDER};
                border-radius: 4px;
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        # ---- slim page (FLAT) ---- #
        self._slim_page = QWidget()
        slim_lay = QHBoxLayout(self._slim_page)
        slim_lay.setContentsMargins(8, 4, 8, 4)
        self._slim_lbl = QLabel("FLAT — awaiting entry")
        self._slim_lbl.setAlignment(Qt.AlignCenter)
        self._slim_lbl.setStyleSheet(_SLIM_STYLE)
        slim_lay.addWidget(self._slim_lbl)
        self._stack.addWidget(self._slim_page)

        # ---- full page (in-position) ---- #
        self._full_page = QWidget()
        self._build_full(self._full_page)
        self._stack.addWidget(self._full_page)

        # default to slim
        self._stack.setCurrentIndex(0)

    # ---------------- layout helpers ---------------- #

    def _build_full(self, page: QWidget) -> None:
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # Header — small section title.
        title = QLabel("POSITION")
        title.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; letter-spacing: 2px; "
            f"font-weight: 700;"
        )
        lay.addWidget(title)

        # Row 1: Side (chip)  |  Size
        row_side = QHBoxLayout()
        row_side.setSpacing(8)
        row_side.addWidget(self._mk_label("SIDE"))
        self._side_chip = QLabel("—")
        self._side_chip.setAlignment(Qt.AlignCenter)
        self._side_chip.setMinimumWidth(54)
        row_side.addWidget(self._side_chip)
        row_side.addStretch(1)
        row_side.addWidget(self._mk_label("SIZE"))
        self._size_val = QLabel("—")
        self._size_val.setStyleSheet(_VALUE_STYLE)
        self._size_val.setMinimumWidth(40)
        self._size_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row_side.addWidget(self._size_val)
        lay.addLayout(row_side)

        # Row 2: Entry  +  verified-tag chip
        row_entry = QHBoxLayout()
        row_entry.setSpacing(8)
        row_entry.addWidget(self._mk_label("ENTRY"))
        self._entry_val = QLabel("—")
        self._entry_val.setStyleSheet(_VALUE_STYLE)
        row_entry.addWidget(self._entry_val)
        self._verified_chip = QLabel("✓ verified")
        self._verified_chip.setStyleSheet(
            f"color: {OK_GREEN}; font-size: 10px; font-weight: 700; "
            "padding: 1px 4px;"
        )
        self._verified_chip.setVisible(False)
        row_entry.addWidget(self._verified_chip)
        row_entry.addStretch(1)
        lay.addLayout(row_entry)

        # Row 3: Current
        row_current = QHBoxLayout()
        row_current.setSpacing(8)
        row_current.addWidget(self._mk_label("CURRENT"))
        self._current_val = QLabel("—")
        self._current_val.setStyleSheet(_VALUE_STYLE)
        row_current.addWidget(self._current_val)
        row_current.addStretch(1)
        lay.addLayout(row_current)

        # Row 4: Stop
        row_stop = QHBoxLayout()
        row_stop.setSpacing(8)
        row_stop.addWidget(self._mk_label("STOP"))
        self._stop_val = QLabel("—")
        self._stop_val.setStyleSheet(_VALUE_STYLE)
        row_stop.addWidget(self._stop_val)
        row_stop.addStretch(1)
        lay.addLayout(row_stop)

        # Row 5: Target
        row_target = QHBoxLayout()
        row_target.setSpacing(8)
        row_target.addWidget(self._mk_label("TARGET"))
        self._target_val = QLabel("—")
        self._target_val.setStyleSheet(_VALUE_STYLE)
        row_target.addWidget(self._target_val)
        row_target.addStretch(1)
        lay.addLayout(row_target)

        # Separator before PnL banner.
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        lay.addWidget(sep)

        # PnL banner — large + bold + color-coded by sign.
        self._pnl_usd = QLabel("PnL  —")
        self._pnl_usd.setAlignment(Qt.AlignCenter)
        self._pnl_usd.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 22px; font-weight: 800;"
        )
        lay.addWidget(self._pnl_usd)

        self._pnl_pts = QLabel("(no verified fill)")
        self._pnl_pts.setAlignment(Qt.AlignCenter)
        self._pnl_pts.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
        )
        lay.addWidget(self._pnl_pts)

    def _mk_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_STYLE)
        return lbl

    # ---------------- public API ---------------- #

    def apply_state(self, state: UiState) -> None:
        """Re-render against the current UiState. Called from the HUD's
        refresh tick. Safe to call frequently — work is only cheap label
        updates plus a single QStackedWidget index toggle."""
        side = state.position_side
        if side == "flat":
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)

        is_long = side == "long"
        side_color = OK_GREEN if is_long else BROKEN_RED
        self._side_chip.setText(side.upper())
        self._side_chip.setStyleSheet(
            f"background-color: {side_color}; color: white; "
            f"padding: 2px 8px; border-radius: 4px; "
            f"font-size: 11px; font-weight: 800; letter-spacing: 1px;"
        )

        size = state.position_size
        self._size_val.setText(str(size) if size is not None else "—")

        self._entry_val.setText(
            f"{state.entry_price:.2f}" if state.entry_price is not None else "—"
        )
        self._verified_chip.setVisible(state.fill_price_source == "position_ocr")

        self._current_val.setText(
            f"{state.last_price:.2f}" if state.last_price is not None else "—"
        )
        self._stop_val.setText(
            f"{state.stop_price:.2f}" if state.stop_price is not None else "—"
        )
        self._target_val.setText(
            f"{state.target_price:.2f}" if state.target_price is not None else "—"
        )

        # PnL banner.
        if state.pnl_usd is not None and state.pnl_points is not None:
            sign_color = OK_GREEN if state.pnl_usd > 0 \
                         else BROKEN_RED if state.pnl_usd < 0 \
                         else TEXT_MUTED
            self._pnl_usd.setText(f"PnL  {state.pnl_usd:+.2f} USD")
            self._pnl_usd.setStyleSheet(
                f"color: {sign_color}; font-size: 22px; font-weight: 800;"
            )
            self._pnl_pts.setText(f"{state.pnl_points:+.2f} pts")
            self._pnl_pts.setStyleSheet(
                f"color: {sign_color}; font-size: 11px; font-weight: 600;"
            )
        else:
            # No verified fill — show muted placeholder + warning glyph.
            self._pnl_usd.setText("PnL  —  ⚠")
            self._pnl_usd.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 22px; font-weight: 800;"
            )
            self._pnl_pts.setText("(no verified fill)")
            self._pnl_pts.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px;"
            )

    @property
    def is_flat_view(self) -> bool:
        """Convenience for tests."""
        return self._stack.currentIndex() == 0
