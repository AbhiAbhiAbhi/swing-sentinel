"""Unit tests for the pure R-multiple expectancy analytics module (issue #5).

All tests run offline on synthetic trade dicts — no Flask, no file I/O.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from core_r_analytics import (
    compute_r_analytics,
    compute_slippage,
    compute_trade_r,
    resolve_initial_sl,
    vol_bucket,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _trade(**overrides):
    """Baseline closed trade dict as supplied by the server layer."""
    d = dict(
        symbol="TEST",
        entry=100.0,
        exit=110.0,
        initial_sl=95.0,
        initial_sl_source="exact",
        current_sl=95.0,
        outcome="T2_WIN",
        setup="PULLBACK",
        grade="",
        sector="",
        vol_ratio=None,
        nifty_regime="",
    )
    d.update(overrides)
    return d


def _row(**overrides):
    """Baseline positions.csv row (as dict of strings, like pandas str cols)."""
    d = {
        "Symbol": "TEST",
        "Entry_Price": "100.0",
        "Closing_Price": "95.0",
        "Current_SL": "95.0",
        "Initial_SL": "",
        "Outcome": "SL_LOSS",
        "T1_Hit_Date": "",
        "Risk_Per_Share": "",
        "Entry_Date": "2026-06-01",
    }
    d.update(overrides)
    return d


# ── compute_trade_r ──────────────────────────────────────────────────────────

def test_r_clean_win():
    # risked 5, made 10 → +2R
    assert compute_trade_r(100.0, 110.0, 95.0) == 2.0


def test_r_sl_loss_is_minus_one():
    assert compute_trade_r(100.0, 95.0, 95.0) == -1.0


def test_r_sl_loss_with_slippage_below_minus_one():
    # stopped out below the stop (gap) → worse than -1R
    r = compute_trade_r(100.0, 93.0, 95.0)
    assert r == -1.4


def test_r_none_when_sl_missing():
    assert compute_trade_r(100.0, 110.0, None) is None


def test_r_none_when_zero_or_negative_risk():
    assert compute_trade_r(100.0, 110.0, 100.0) is None
    assert compute_trade_r(100.0, 110.0, 105.0) is None


def test_r_none_when_prices_missing():
    assert compute_trade_r(None, 110.0, 95.0) is None
    assert compute_trade_r(100.0, None, 95.0) is None


# ── resolve_initial_sl priority order ────────────────────────────────────────

def test_resolve_exact_column_wins():
    row = _row(Initial_SL="94.0")
    pm = {"TEST": {"price_path": {"stop_price": 93.0}}}
    sl, src = resolve_initial_sl(row, pm)
    assert sl == 94.0
    assert src == "exact"


def test_resolve_pm_json_second():
    row = _row()
    pm = {"TEST": {"price_path": {"stop_price": 93.0}}}
    sl, src = resolve_initial_sl(row, pm)
    assert sl == 93.0
    assert src == "pm_json"


def test_resolve_pm_json_prefers_symbol_date_key():
    # same symbol traded twice → per-entry-date PM file must win
    row = _row(Entry_Date="2026-05-21")
    pm = {
        "TEST": {"price_path": {"stop_price": 90.0}},
        "TEST_2026-05-21": {"price_path": {"stop_price": 92.0}},
    }
    sl, src = resolve_initial_sl(row, pm)
    assert sl == 92.0
    assert src == "pm_json"


def test_resolve_untriggered_sl_third():
    # SL_LOSS that never hit T1 → Current_SL is still the original stop
    row = _row(Current_SL="95.5")
    sl, src = resolve_initial_sl(row, {})
    assert sl == 95.5
    assert src == "untriggered_sl"


def test_resolve_untriggered_requires_no_t1():
    # T1 was hit → Current_SL has been trailed, cannot trust it
    row = _row(T1_Hit_Date="2026-06-05")
    sl, src = resolve_initial_sl(row, {})
    assert sl is None
    assert src == "unrecoverable"


def test_resolve_risk_per_share_fourth():
    row = _row(Outcome="T2_WIN", T1_Hit_Date="2026-06-05", Risk_Per_Share="6.0")
    sl, src = resolve_initial_sl(row, {})
    assert sl == 94.0
    assert src == "risk_per_share"


def test_resolve_unrecoverable():
    row = _row(Outcome="T2_WIN", T1_Hit_Date="2026-06-05")
    sl, src = resolve_initial_sl(row, {})
    assert sl is None
    assert src == "unrecoverable"


# ── vol_bucket ───────────────────────────────────────────────────────────────

def test_vol_buckets():
    assert vol_bucket(1.0) == "<1.5"
    assert vol_bucket(1.5) == "1.5-2.5"
    assert vol_bucket(2.0) == "1.5-2.5"
    assert vol_bucket(3.0) == ">2.5"
    assert vol_bucket(None) == "UNKNOWN"
    assert vol_bucket("") == "UNKNOWN"
    assert vol_bucket("junk") == "UNKNOWN"


# ── compute_slippage ─────────────────────────────────────────────────────────

def test_slippage_on_sl_exit():
    # intended stop 74.91, filled at 73.70 → 1.21 rupees of slippage
    t = _trade(entry=80.0, exit=73.70, initial_sl=74.91, current_sl=74.91,
               outcome="SL_LOSS")
    s = compute_slippage(t)
    assert s is not None
    assert round(s["slippage_rupees"], 2) == 1.21
    # as fraction of initial risk (80 - 74.91 = 5.09)
    assert round(s["slippage_r"], 3) == round(1.21 / 5.09, 3)


def test_slippage_ignores_non_sl_exits():
    t = _trade(outcome="T2_WIN")
    assert compute_slippage(t) is None


def test_slippage_zero_when_filled_at_stop():
    t = _trade(entry=100.0, exit=95.0, initial_sl=95.0, current_sl=95.0,
               outcome="SL_LOSS")
    s = compute_slippage(t)
    assert s["slippage_rupees"] == 0.0


# ── compute_r_analytics ──────────────────────────────────────────────────────

def test_analytics_empty_input():
    out = compute_r_analytics([])
    assert out["trades_with_r"] == 0
    assert out["trades_excluded_no_sl"] == 0
    assert out["expectancy_r"] is None
    assert out["breakdowns"] == {}


def test_analytics_expectancy_and_counts():
    trades = [
        _trade(exit=110.0),                                  # +2R win
        _trade(exit=95.0, outcome="SL_LOSS"),                # -1R loss
        _trade(initial_sl=None, initial_sl_source="unrecoverable"),  # excluded
    ]
    out = compute_r_analytics(trades)
    assert out["trades_with_r"] == 2
    assert out["trades_excluded_no_sl"] == 1
    assert out["expectancy_r"] == 0.5   # mean(2, -1)
    assert out["win_r_avg"] == 2.0
    assert out["loss_r_avg"] == -1.0


def test_analytics_breakdowns_unknown_buckets():
    trades = [
        _trade(exit=110.0, setup="PULLBACK", grade="", sector="", vol_ratio=None,
               nifty_regime=""),
        _trade(exit=95.0, outcome="SL_LOSS", setup="BREAKOUT", grade="A",
               sector="Metals", vol_ratio=2.0, nifty_regime="UPTREND"),
    ]
    out = compute_r_analytics(trades)
    b = out["breakdowns"]
    assert b["setup"]["PULLBACK"]["count"] == 1
    assert b["setup"]["PULLBACK"]["expectancy_r"] == 2.0
    assert b["setup"]["BREAKOUT"]["losses"] == 1
    assert b["grade"]["UNKNOWN"]["count"] == 1
    assert b["grade"]["A"]["count"] == 1
    assert b["sector"]["UNKNOWN"]["count"] == 1
    assert b["vol_bucket"]["1.5-2.5"]["count"] == 1
    assert b["vol_bucket"]["UNKNOWN"]["count"] == 1
    assert b["nifty_regime"]["UPTREND"]["count"] == 1
    assert b["nifty_regime"]["UNKNOWN"]["count"] == 1


def test_analytics_slippage_block():
    trades = [
        _trade(entry=80.0, exit=73.70, initial_sl=74.91, current_sl=74.91,
               outcome="SL_LOSS"),
        _trade(exit=110.0, outcome="T2_WIN"),
    ]
    out = compute_r_analytics(trades)
    s = out["slippage"]
    assert s["n_sl_exits"] == 1
    assert round(s["avg_slippage_rupees"], 2) == 1.21
    # slippage-adjusted expectancy exists and is a number
    assert isinstance(s["expectancy_r_slippage_adjusted"], float)
