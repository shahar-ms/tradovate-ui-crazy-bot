"""
TradeFlow — drive a Supervisor through a full trade lifecycle without
needing real OCR, a real screen, or a network connection.

Wraps the same handlers the live OCR watchers + price stream call:

  open(side, entry, size)  -> _on_entry_price_changed + _on_position_size_changed
  scale(new_signed_size, ...) -> ditto for scale-in / scale-out / flip
  tick(price)              -> sets state.last_price (PriceStream's job)
  close()                  -> size -> 0 + entry -> None
  hud_click(action)        -> goes through UiController.hud_click when wired

Used by:
  - tests/test_trade_flow_e2e.py (synchronous; pyautogui mocked)
  - app/ui/demo_hud_trade.py     (live; HUD open, events scheduled via
                                  QTimer so the operator can watch)

Every event takes a snapshot so the caller can assert / inspect any moment
in the lifecycle, and `realized_pnl()` reads the last in-position snapshot
to recover the realized P&L on close (the LAST tick before close IS the
exit price).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from app.orchestrator.supervisor import Supervisor
from app.strategy.pnl import compute_pnl

SideT = Literal["long", "short"]


@dataclass
class TradeSnapshot:
    """One observable moment in the trade lifecycle."""
    label: str
    side: str
    size: int
    fill: Optional[float]
    last_price: Optional[float]
    pnl_points: Optional[float]
    pnl_usd: Optional[float]


class TradeFlow:
    def __init__(self, sup: Supervisor, controller=None):
        """If `controller` is provided, hud_click() routes through the same
        UiController.hud_click() the live HUD calls — including the
        pyautogui click. Tests should monkeypatch pyautogui.click first if
        they want to capture the coordinates without actually clicking."""
        self.sup = sup
        self.controller = controller
        self.snapshots: list[TradeSnapshot] = []
        self._snapshot("init")

    # ---- operator inputs (HUD clicks) ---- #

    def hud_click(self, action: str) -> None:
        """Operator presses BUY / SELL / CANCEL ALL on the HUD. Goes
        through the real UiController when wired so the click path is
        exercised end-to-end; otherwise just records the intent on
        supervisor state (matches what the controller does internally)."""
        if self.controller is not None:
            self.controller.hud_click(action)
        else:
            self.sup.state.last_manual_click_action = action
        self._snapshot(f"hud_click {action}")

    # ---- broker-side inputs (what the OCR watchers + price stream feed) ---- #

    def open(self, side: SideT, entry: float, size: int = 1) -> None:
        """Mimic broker fill: entry_price cell + signed size cell update."""
        if size <= 0:
            raise ValueError("size must be a positive contract count; sign comes from `side`")
        signed = size if side == "long" else -size
        self.sup._on_entry_price_changed(entry)
        self.sup._on_position_size_changed(signed)
        self._snapshot(f"open {side} {size}@{entry}")

    def scale(self, new_size_signed: int, new_entry: Optional[float] = None) -> None:
        """Scale in / out (same side, new contract count) or flip side. If
        new_entry is provided, also push it through the entry-price watcher."""
        if new_entry is not None:
            self.sup._on_entry_price_changed(new_entry)
        self.sup._on_position_size_changed(new_size_signed)
        self._snapshot(f"scale -> size={new_size_signed} entry={new_entry}")

    def tick(self, price: float) -> None:
        """Inject a live-price tick. PriceStream normally writes
        state.last_price; we write it directly to skip the queue."""
        self.sup.state.last_price = price
        self._snapshot(f"tick {price}")

    def close(self) -> None:
        """Mimic broker close: signed size goes to 0 and the entry cell
        clears. The order matches what the real watchers do — size first,
        entry blanks out shortly after."""
        self.sup._on_position_size_changed(0)
        self.sup._on_entry_price_changed(None)
        self._snapshot("close")

    # ---- inspection helpers ---- #

    def realized_pnl(self) -> tuple[Optional[float], Optional[float]]:
        """Realized PnL on the LAST in-position snapshot before close.
        That snapshot's last_price IS the exit price."""
        in_position = [s for s in self.snapshots
                       if s.side in ("long", "short") and s.size > 0]
        if not in_position:
            return None, None
        last = in_position[-1]
        if last.fill is None or last.last_price is None:
            return None, None
        return compute_pnl(last.fill, last.last_price, last.side,  # type: ignore[arg-type]
                           contracts=last.size)

    @property
    def latest(self) -> TradeSnapshot:
        return self.snapshots[-1]

    def _snapshot(self, label: str) -> None:
        s = self.sup.state
        side = s.current_position_side
        size = s.position_size or 0
        fill = s.last_fill_price
        last_price = s.last_price
        if fill is not None and last_price is not None and side in ("long", "short"):
            pts, usd = compute_pnl(fill, last_price, side,  # type: ignore[arg-type]
                                   contracts=max(1, size))
        else:
            pts, usd = None, None
        self.snapshots.append(TradeSnapshot(
            label=label, side=side, size=size, fill=fill,
            last_price=last_price, pnl_points=pts, pnl_usd=usd,
        ))
