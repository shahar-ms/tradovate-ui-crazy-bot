from app.strategy.pnl import compute_pnl, points_pnl, usd_pnl


def test_points_pnl_long():
    assert points_pnl(100.0, 102.25, "long") == 2.25


def test_points_pnl_short():
    assert points_pnl(100.0, 98.5, "short") == 1.5


def test_points_pnl_flat():
    assert points_pnl(100.0, 105.0, "flat") == 0.0


def test_usd_pnl_mnq_default():
    # MNQ: $2 per point
    assert usd_pnl(2.25) == 4.5


def test_usd_pnl_unknown_symbol_falls_back_to_mnq():
    assert usd_pnl(1.0, contract_symbol="ZZZ") == 2.0


def test_usd_pnl_respects_contracts():
    assert usd_pnl(1.0, contract_symbol="MNQ", contracts=3) == 6.0


def test_usd_pnl_nq():
    # NQ: $20 per point
    assert usd_pnl(1.0, contract_symbol="NQ") == 20.0


def test_compute_pnl_returns_none_when_entry_missing():
    assert compute_pnl(None, 100.0, "long") == (None, None)


def test_compute_pnl_returns_none_when_current_missing():
    assert compute_pnl(100.0, None, "long") == (None, None)


def test_compute_pnl_returns_none_when_flat():
    assert compute_pnl(100.0, 105.0, "flat") == (None, None)


def test_compute_pnl_long_full():
    pts, dollars = compute_pnl(100.0, 103.0, "long")
    assert pts == 3.0
    assert dollars == 6.0


def test_compute_pnl_short_full():
    pts, dollars = compute_pnl(100.0, 98.25, "short")
    assert pts == 1.75
    assert dollars == 3.5


def test_compute_pnl_negative_long():
    pts, dollars = compute_pnl(100.0, 98.0, "long")
    assert pts == -2.0
    assert dollars == -4.0
