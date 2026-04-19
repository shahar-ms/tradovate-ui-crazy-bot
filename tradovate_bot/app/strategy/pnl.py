"""
Pure PnL math, used by the UI layer to display verified unrealized PnL.

Contract specs (CME Micro E-mini Nasdaq = MNQ):
  - 0.25 index points = $0.50
  - $2.00 per full point per contract

Kept side-effect-free: no Qt, no threads, no I/O.
"""

from __future__ import annotations

from typing import Literal, Optional

SideT = Literal["flat", "long", "short"]

# Dollar value of one full index point per contract, keyed by symbol.
_USD_PER_POINT: dict[str, float] = {
    "MNQ": 2.0,   # micro NASDAQ-100
    "NQ": 20.0,   # e-mini NASDAQ-100
    "MES": 5.0,   # micro S&P 500
    "ES": 50.0,   # e-mini S&P 500
}


def points_pnl(entry_price: float, current_price: float, side: SideT) -> float:
    """Unrealized PnL in index points for a single contract."""
    if side == "long":
        return current_price - entry_price
    if side == "short":
        return entry_price - current_price
    return 0.0


def usd_pnl(points: float, contract_symbol: str = "MNQ",
            contracts: int = 1) -> float:
    """Convert point PnL to USD. Unknown symbol falls back to MNQ."""
    multiplier = _USD_PER_POINT.get(contract_symbol.upper(), _USD_PER_POINT["MNQ"])
    return points * multiplier * contracts


def compute_pnl(entry_price: Optional[float],
                current_price: Optional[float],
                side: SideT,
                contract_symbol: str = "MNQ",
                contracts: int = 1) -> tuple[Optional[float], Optional[float]]:
    """
    Return (points, usd). Returns (None, None) when either input is missing
    or the position is flat — the UI must then show `PnL: —`, not a number.
    """
    if entry_price is None or current_price is None or side == "flat":
        return None, None
    pts = points_pnl(entry_price, current_price, side)
    return pts, usd_pnl(pts, contract_symbol, contracts)
