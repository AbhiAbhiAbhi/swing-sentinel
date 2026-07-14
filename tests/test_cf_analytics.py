"""
Offline unit tests for core_cf_analytics (issue #6) — no Flask, no yfinance,
no file I/O. Bars are synthetic DataFrames.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from core_cf_analytics import (  # noqa: E402
    CF_HORIZONS,
    aggregate_cf_by_reason,
    bucket_prune_reason,
    compute_cf_for_row,
)


def _row(**overrides):
    row = {
        "Symbol": "TEST",
        "Status": "PRUNED",
        "Prune_Reason": "Trend structure broke",
        "Prune_Date": "2026-01-01",
        "Target_1": 110.0,
        "Target_2": 120.0,
        "Current_SL": 95.0,
        "Initial_SL": "",
    }
    row.update(overrides)
    return row


def _bars(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "Close": closes,
        "High": highs if highs is not None else [c * 1.01 for c in closes],
        "Low": lows if lows is not None else [c * 0.99 for c in closes],
    }, index=pd.bdate_range("2026-01-01", periods=n))


def _flat_bars(n, price=100.0):
    return _bars([price] * n)


# ── compute_cf_for_row ──────────────────────────────────────────────────────

def test_returns_at_horizons():
    closes = [100.0] + [100.0 + i for i in range(1, 36)]  # drifts up 1/day
    cf = compute_cf_for_row(_row(Target_1=999, Target_2=1000, Current_SL=1), _bars(closes))
    assert cf["CF_Return_10d"] == 10.0
    assert cf["CF_Return_20d"] == 20.0
    assert cf["CF_Return_30d"] == 30.0
    assert cf["CF_Complete"] is True


def test_t1_first_touch():
    bars = _flat_bars(35)
    bars.iloc[5, bars.columns.get_loc("High")] = 111.0  # touches T1 only
    cf = compute_cf_for_row(_row(), bars)
    assert cf["CF_Would_Have_Hit"] == "T1"


def test_t2_beats_t1_when_both_touched_same_day():
    bars = _flat_bars(35)
    bars.iloc[5, bars.columns.get_loc("High")] = 125.0  # clears T2
    cf = compute_cf_for_row(_row(), bars)
    assert cf["CF_Would_Have_Hit"] == "T2"


def test_sl_first_touch():
    bars = _flat_bars(35)
    bars.iloc[3, bars.columns.get_loc("Low")] = 94.0
    bars.iloc[10, bars.columns.get_loc("High")] = 125.0  # target later — too late
    cf = compute_cf_for_row(_row(), bars)
    assert cf["CF_Would_Have_Hit"] == "SL"


def test_same_day_tie_is_conservative_sl():
    bars = _flat_bars(35)
    bars.iloc[4, bars.columns.get_loc("High")] = 125.0
    bars.iloc[4, bars.columns.get_loc("Low")] = 90.0
    cf = compute_cf_for_row(_row(), bars)
    assert cf["CF_Would_Have_Hit"] == "SL"


def test_none_when_nothing_touched():
    cf = compute_cf_for_row(_row(), _flat_bars(35))
    assert cf["CF_Would_Have_Hit"] == "NONE"


def test_prune_day_bar_excluded_from_touch():
    bars = _flat_bars(35)
    bars.iloc[0, bars.columns.get_loc("High")] = 125.0  # prune-day spike ignored
    cf = compute_cf_for_row(_row(), bars)
    assert cf["CF_Would_Have_Hit"] == "NONE"


def test_missing_levels_unresolved():
    cf = compute_cf_for_row(
        _row(Target_1="", Target_2="", Current_SL="", Initial_SL=""),
        _flat_bars(35),
    )
    assert cf["CF_Would_Have_Hit"] == "UNRESOLVED"


def test_initial_sl_fallback():
    bars = _flat_bars(35)
    bars.iloc[2, bars.columns.get_loc("Low")] = 89.0
    cf = compute_cf_for_row(_row(Current_SL="", Initial_SL=90.0), bars)
    assert cf["CF_Would_Have_Hit"] == "SL"


def test_partial_horizon_only_10d_filled():
    cf = compute_cf_for_row(_row(), _flat_bars(12))
    assert cf["CF_Return_10d"] == 0.0
    assert cf["CF_Return_20d"] == ""
    assert cf["CF_Return_30d"] == ""
    assert cf["CF_Complete"] is False


def test_empty_bars():
    cf = compute_cf_for_row(_row(), pd.DataFrame())
    assert cf["CF_Would_Have_Hit"] == "UNRESOLVED"
    assert cf["CF_Complete"] is False
    assert all(cf[f"CF_Return_{h}d"] == "" for h in CF_HORIZONS)


# ── bucketing & aggregation ─────────────────────────────────────────────────

def test_bucketing():
    assert bucket_prune_reason("Safety gates failed: ADX weak") == "Safety gates failed"
    assert bucket_prune_reason("Trend structure broke below EMA50") == "Trend structure broke"
    assert bucket_prune_reason("Absent from data feed for 3 cycles") == "Absent from data feed"
    assert bucket_prune_reason("Error during analysis (timeout)") == "Analysis error"
    assert bucket_prune_reason("") == "Unspecified"
    assert bucket_prune_reason("nan") == "Unspecified"
    assert bucket_prune_reason("something exotic") == "Other"


def _agg_row(hit, r10=1.0, reason="Trend structure broke"):
    return {"Prune_Reason": reason, "CF_Would_Have_Hit": hit,
            "CF_Return_10d": r10, "CF_Return_20d": r10, "CF_Return_30d": r10}


def test_aggregate_hit_rates_and_verdict():
    rows = [_agg_row("T1"), _agg_row("T2"), _agg_row("T1"),
            _agg_row("SL"), _agg_row("NONE"), _agg_row("UNRESOLVED")]
    out = aggregate_cf_by_reason(rows)
    assert out["total"] == 6 and out["resolved"] == 5
    b = out["buckets"][0]
    assert b["reason"] == "Trend structure broke"
    assert b["count"] == 6 and b["resolved"] == 5
    assert b["t_hit_pct"] == 60.0 and b["sl_hit_pct"] == 20.0
    assert b["verdict"] == "PRUNING_WINNERS"


def test_aggregate_filter_alpha_verdict():
    rows = [_agg_row("SL", r10=-5.0) for _ in range(5)]
    b = aggregate_cf_by_reason(rows)["buckets"][0]
    assert b["verdict"] == "FILTER_HAS_ALPHA"
    assert b["avg_return_10d"] == -5.0


def test_aggregate_insufficient_data():
    rows = [_agg_row("T1"), _agg_row("T1")]
    b = aggregate_cf_by_reason(rows)["buckets"][0]
    assert b["verdict"] == "INSUFFICIENT_DATA"


def test_aggregate_empty():
    out = aggregate_cf_by_reason([])
    assert out == {"buckets": [], "total": 0, "resolved": 0}
