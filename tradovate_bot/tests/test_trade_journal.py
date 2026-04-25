"""
Direct tests for TradeJournal — the trade-boundary state machine and
SQLite persistence. The supervisor wiring is exercised separately in
test_trade_flow_e2e.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.orchestrator.trade_journal import TradeJournal, TradeRecord


# ---- helpers ---- #


def _journal(tmp: bool = False, db_path: Path | str = ":memory:") -> TradeJournal:
    return TradeJournal(db_path=db_path, session_id="test-session")


# ---- single-trade lifecycle ---- #


def test_journal_records_simple_long_trade():
    j = _journal()
    j.position_observed(side="long", size=1, fill_price=26680.0,
                        last_price=26680.0, ts_ms=1000)
    j.position_observed(side="long", size=1, fill_price=26680.0,
                        last_price=26700.5, ts_ms=2000)   # mid-trade tick
    j.position_observed(side="flat", size=0, fill_price=None,
                        last_price=26700.5, ts_ms=3000)   # close

    assert j.session_count() == 1
    rec = j.session_trades[0]
    assert rec.side == "long"
    assert rec.entry_price == 26680.0
    assert rec.exit_price == 26700.5
    assert rec.max_size == 1
    assert rec.pnl_points == pytest.approx(20.5)
    assert rec.pnl_usd == pytest.approx(41.0)        # $2/pt * 1
    assert rec.entry_ts_ms == 1000
    assert rec.exit_ts_ms == 3000


def test_journal_records_simple_short_trade():
    j = _journal()
    j.position_observed("short", 2, 26700.0, 26700.0, ts_ms=1000)
    j.position_observed("flat",  0, None,    26710.25, ts_ms=2000)

    rec = j.session_trades[0]
    assert rec.side == "short"
    assert rec.max_size == 2
    assert rec.pnl_points == pytest.approx(-10.25)
    assert rec.pnl_usd == pytest.approx(-41.0)       # $2/pt * 2 contracts


# ---- ordering robustness: entry-price arrives before / after size ---- #


def test_journal_handles_size_first_then_entry_price():
    """PositionWatcher fires before EntryPriceWatcher: open with entry=None,
    then a follow-up observation backfills it."""
    j = _journal()
    j.position_observed("long", 1, None, None, ts_ms=1000)        # size watcher
    j.position_observed("long", 1, 26680.0, None, ts_ms=1100)     # entry watcher
    j.position_observed("long", 1, 26680.0, 26690.0, ts_ms=1500)  # tick
    j.position_observed("flat", 0, None, 26690.0, ts_ms=2000)     # close

    assert j.session_count() == 1
    assert j.session_trades[0].entry_price == 26680.0
    assert j.session_trades[0].exit_price == 26690.0


def test_journal_handles_entry_price_first_then_size():
    """EntryPriceWatcher fires before PositionWatcher: while still flat,
    the entry observation is a no-op; once size opens we use the entry."""
    j = _journal()
    j.position_observed("flat", 0, 26680.0, None, ts_ms=900)      # entry watcher (we're still flat in journal eyes)
    j.position_observed("long", 1, 26680.0, None, ts_ms=1000)     # size watcher
    j.position_observed("flat", 0, None,    26700.0, ts_ms=2000)

    assert j.session_count() == 1
    assert j.session_trades[0].entry_price == 26680.0


# ---- scaling + flips ---- #


def test_journal_max_size_tracks_peak_during_scale_in():
    j = _journal()
    j.position_observed("long", 1, 26680.0, 26680.0, ts_ms=1000)
    j.position_observed("long", 2, 26680.0, 26690.0, ts_ms=1500)   # scaled to 2
    j.position_observed("long", 1, 26680.0, 26695.0, ts_ms=1700)   # scaled back
    j.position_observed("flat", 0, None,    26700.0, ts_ms=2000)

    rec = j.session_trades[0]
    assert rec.max_size == 2, "max_size should remember the peak across scaling"
    assert rec.final_size == 1
    # PnL uses max_size so $-USD reflects peak exposure during the trade:
    # 20 pts * $2/pt * 2 contracts = $80
    assert rec.pnl_usd == pytest.approx(80.0)


def test_journal_flip_records_two_trades():
    """Direct long->short reversal must produce TWO trade records: one
    closed at the flip price, one opened on the new side."""
    j = _journal()
    j.position_observed("long",  1, 26680.0, 26680.0, ts_ms=1000)
    j.position_observed("long",  1, 26680.0, 26690.0, ts_ms=1500)
    j.position_observed("short", 1, 26690.0, 26690.0, ts_ms=1600)  # flip
    j.position_observed("flat",  0, None,    26680.0, ts_ms=2500)  # close short

    assert j.session_count() == 2
    long_trade, short_trade = j.session_trades
    assert long_trade.side == "long"
    assert long_trade.exit_price == 26690.0          # closed at flip price
    assert short_trade.side == "short"
    assert short_trade.entry_price == 26690.0
    assert short_trade.exit_price == 26680.0
    assert short_trade.pnl_points == pytest.approx(10.0)


# ---- incomplete data: drop, don't lie ---- #


def test_journal_drops_open_with_no_entry_price():
    """If we never observed an entry price, the close has nothing to
    anchor PnL against — the record is dropped, not invented."""
    j = _journal()
    j.position_observed("long", 1, None,    None,    ts_ms=1000)
    j.position_observed("flat", 0, None,    26690.0, ts_ms=2000)

    assert j.session_count() == 0


def test_journal_drops_open_with_no_exit_price():
    """Closing without a known last_price leaves us unable to compute the
    exit. Drop rather than fabricate."""
    j = _journal()
    j.position_observed("long", 1, 26680.0, None, ts_ms=1000)
    j.position_observed("flat", 0, None,    None, ts_ms=2000)

    assert j.session_count() == 0


# ---- SQLite persistence: round-trip across journal lifetimes ---- #


def test_journal_persists_trades_to_sqlite(tmp_path: Path):
    db = tmp_path / "trades.sqlite"

    j1 = TradeJournal(db_path=db, session_id="run-1")
    j1.position_observed("long", 1, 26680.0, 26680.0, ts_ms=1000)
    j1.position_observed("flat", 0, None,    26700.0, ts_ms=2000)
    j1.close()

    # Reopen — file should already exist with the prior session's row.
    j2 = TradeJournal(db_path=db, session_id="run-2")
    rows = j2.all_trades()
    assert len(rows) == 1
    assert rows[0].session_id == "run-1"
    assert rows[0].pnl_points == pytest.approx(20.0)

    # Add another trade in run-2.
    j2.position_observed("short", 2, 26700.0, 26700.0, ts_ms=3000)
    j2.position_observed("flat",  0, None,    26695.0, ts_ms=4000)
    j2.close()

    # Third opener reads BOTH sessions.
    j3 = TradeJournal(db_path=db, session_id="run-3")
    all_rows = j3.all_trades()
    assert len(all_rows) == 2
    sessions = {r.session_id for r in all_rows}
    assert sessions == {"run-1", "run-2"}
    j3.close()


def test_journal_sqlite_schema_has_indexes(tmp_path: Path):
    """Sanity check that the indexes the offline-analysis queries rely on
    actually exist after the schema is created."""
    db = tmp_path / "trades.sqlite"
    TradeJournal(db_path=db, session_id="test").close()

    conn = sqlite3.connect(db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_trades_%'"
    )
    names = {row[0] for row in cur.fetchall()}
    conn.close()

    assert "idx_trades_session" in names
    assert "idx_trades_exit_ts" in names
