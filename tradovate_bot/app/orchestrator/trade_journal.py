"""
TradeJournal — records completed trades to memory + SQLite.

Design: ONE observer hook (`position_observed`) called by the supervisor
after every relevant state mutation. The journal runs an internal little
state machine that decides whether the observation starts a new trade,
accumulates a scale-in, or finalizes the current one.

This keeps the supervisor side trivial — it just describes "what the
broker shows now" — and puts all the trade-boundary logic in one place
where it can be tested in isolation.

In-memory: `session_trades` is the list shown on the HUD (this run only).
On disk:   `trades.sqlite` accumulates every completed trade across every
           run so the data is available for offline analysis / training.

Thread safety: position / entry-price watchers run on separate threads
and can both call position_observed concurrently. A simple lock around
the state-machine transitions keeps `_open` and `session_trades` consistent.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from app.strategy.pnl import points_pnl, usd_pnl
from app.utils.time_utils import now_ms

log = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """One completed trade. Fields are deliberately flat + primitive so
    SQLite rows and JSON dumps line up trivially."""
    session_id: str
    side: str                       # "long" | "short"
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    exit_price: float
    max_size: int                   # peak contract count during the trade
    final_size: int                 # contracts at the moment before close
    pnl_points: float
    pnl_usd: float
    contract_symbol: str = "MNQ"
    id: Optional[int] = None        # rowid, set after insert


@dataclass
class _OpenTradeAccumulator:
    """Internal scratch object held between trade open and close."""
    side: str
    entry: Optional[float]
    entry_ts_ms: int
    max_size: int
    current_size: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_ts_ms INTEGER NOT NULL,
    exit_ts_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    max_size INTEGER NOT NULL,
    final_size INTEGER NOT NULL,
    pnl_points REAL NOT NULL,
    pnl_usd REAL NOT NULL,
    contract_symbol TEXT NOT NULL DEFAULT 'MNQ'
);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_exit_ts ON trades(exit_ts_ms);
"""


class TradeJournal:
    def __init__(self, db_path: Path | str, session_id: str,
                 contract_symbol: str = "MNQ"):
        """
        db_path: file used for the SQLite database. ':memory:' is supported
                 (used by tests).
        session_id: tag for every row written this run; lets callers query
                    "trades from this session" easily later.
        """
        self.db_path = str(db_path)
        self.session_id = session_id
        self.contract_symbol = contract_symbol
        self.session_trades: list[TradeRecord] = []
        self._open: Optional[_OpenTradeAccumulator] = None
        self._lock = threading.Lock()

        # check_same_thread=False because watcher threads call into us;
        # the lock above serializes access so it's still correct.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        log.info("TradeJournal opened: db=%s session=%s", self.db_path, session_id)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                log.debug("TradeJournal close failed", exc_info=True)

    # ---- single observer hook ---- #

    def position_observed(self, side: str, size: int,
                          fill_price: Optional[float],
                          last_price: Optional[float],
                          ts_ms: Optional[int] = None) -> None:
        """Called by the supervisor after every relevant state change.
        Idempotent — works the same regardless of whether the size watcher
        or the entry-price watcher fired first.

        Args:
            side: "long" | "short" | "flat"
            size: absolute contract count (>=0)
            fill_price: broker's verified entry price; None when flat or
                not yet observed.
            last_price: most recent live price (the exit price when this
                observation finalizes the trade).
            ts_ms: observation timestamp; defaults to now.
        """
        ts_ms = ts_ms if ts_ms is not None else now_ms()
        in_position = side in ("long", "short") and size > 0

        with self._lock:
            if in_position:
                if self._open is None:
                    self._open = _OpenTradeAccumulator(
                        side=side, entry=fill_price, entry_ts_ms=ts_ms,
                        max_size=size, current_size=size,
                    )
                elif self._open.side != side:
                    # Direct flip (long<->short). Close the prior trade at
                    # the current market and open a new one for the new side.
                    self._finalize_locked(exit_price=last_price, exit_ts_ms=ts_ms)
                    self._open = _OpenTradeAccumulator(
                        side=side, entry=fill_price, entry_ts_ms=ts_ms,
                        max_size=size, current_size=size,
                    )
                else:
                    # Same side; possibly scale-in / scale-out / entry-price
                    # refresh from the entry-price watcher.
                    self._open.max_size = max(self._open.max_size, size)
                    self._open.current_size = size
                    if self._open.entry is None and fill_price is not None:
                        self._open.entry = fill_price
            else:
                # Flat. Finalize anything still open.
                if self._open is not None:
                    self._finalize_locked(exit_price=last_price, exit_ts_ms=ts_ms)

    # ---- finalize + persist ---- #

    def _finalize_locked(self, exit_price: Optional[float],
                         exit_ts_ms: int) -> None:
        """Close the current open accumulator. Caller holds self._lock."""
        ot = self._open
        if ot is None:
            return
        # Drop the open regardless — even if we can't compute a record we
        # don't want a stale accumulator hanging around.
        self._open = None
        if ot.entry is None or exit_price is None:
            log.warning(
                "TradeJournal: dropping incomplete trade (side=%s entry=%s exit=%s)",
                ot.side, ot.entry, exit_price,
            )
            return
        pts = points_pnl(ot.entry, exit_price, ot.side)  # type: ignore[arg-type]
        usd = usd_pnl(pts, contract_symbol=self.contract_symbol,
                      contracts=max(1, ot.max_size))
        record = TradeRecord(
            session_id=self.session_id,
            side=ot.side,
            entry_ts_ms=ot.entry_ts_ms,
            exit_ts_ms=exit_ts_ms,
            entry_price=ot.entry,
            exit_price=exit_price,
            max_size=ot.max_size,
            final_size=ot.current_size,
            pnl_points=pts,
            pnl_usd=usd,
            contract_symbol=self.contract_symbol,
        )
        rowid = self._persist_locked(record)
        record.id = rowid
        self.session_trades.append(record)
        log.info(
            "TradeJournal: recorded %s %d %.4f -> %.4f  pnl=%+.2fpts %+.2fUSD",
            record.side, record.max_size, record.entry_price,
            record.exit_price, record.pnl_points, record.pnl_usd,
        )

    def _persist_locked(self, record: TradeRecord) -> int:
        cur = self._conn.execute(
            "INSERT INTO trades(session_id, side, entry_ts_ms, exit_ts_ms, "
            "entry_price, exit_price, max_size, final_size, pnl_points, "
            "pnl_usd, contract_symbol) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record.session_id, record.side, record.entry_ts_ms,
             record.exit_ts_ms, record.entry_price, record.exit_price,
             record.max_size, record.final_size, record.pnl_points,
             record.pnl_usd, record.contract_symbol),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    # ---- read helpers ---- #

    def session_count(self) -> int:
        return len(self.session_trades)

    def all_trades(self) -> list[TradeRecord]:
        """Read every trade from disk (across all sessions). Useful for
        offline analysis / model training."""
        cur = self._conn.execute(
            "SELECT id, session_id, side, entry_ts_ms, exit_ts_ms, "
            "entry_price, exit_price, max_size, final_size, pnl_points, "
            "pnl_usd, contract_symbol FROM trades ORDER BY exit_ts_ms ASC"
        )
        out: list[TradeRecord] = []
        for row in cur.fetchall():
            out.append(TradeRecord(
                id=row[0], session_id=row[1], side=row[2],
                entry_ts_ms=row[3], exit_ts_ms=row[4],
                entry_price=row[5], exit_price=row[6],
                max_size=row[7], final_size=row[8],
                pnl_points=row[9], pnl_usd=row[10],
                contract_symbol=row[11],
            ))
        return out
