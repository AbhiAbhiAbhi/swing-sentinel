"""Unit tests for the pure functions of the SL post-mortem engine (issue #3).

All tests run offline on synthetic OHLCV frames — no yfinance, no LLM.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from core_post_mortem import (
    analyze_price_path,
    build_weekly_digest_text,
    classify,
    derive_app_gaps,
    ema20_from_frame,
    evaluate_tight_sl_recovery,
    regime_from_frame,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _bars(rows, start="2026-06-01"):
    """rows: list of (open, high, low, close) tuples → daily OHLCV frame."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(
        {
            "Open":  [r[0] for r in rows],
            "High":  [r[1] for r in rows],
            "Low":   [r[2] for r in rows],
            "Close": [r[3] for r in rows],
            "Volume": [100000] * len(rows),
        },
        index=idx,
    )


def _pp(**overrides):
    """Baseline price_path dict — quiet grind, no signals."""
    d = dict(
        descent_type="GRIND",
        worst_gap_down_pct=None,
        exit_open_below_stop=False,
        t1_progress_pct=10.0,
        almost_worked=False,
        closed_below_entry_within_3_bars=False,
    )
    d.update(overrides)
    return d


def _cd(**overrides):
    """Baseline condition_diff dict — nothing notable happened."""
    d = dict(
        earnings_in_window=None,
        earnings_date=None,
        nifty_regime_entry=None,
        nifty_regime_exit=None,
        nifty_change_pct=None,
        sector_pct_ema20_entry=None,
        sector_pct_ema20_exit=None,
        sl_distance_atr=None,
    )
    d.update(overrides)
    return d


def _row(**overrides):
    d = {"Buy_Vol_Ratio": "1.5", "Buy_False_Breakout_Risk": "LOW", "Buy_ATR_Pct": "2.0"}
    d.update(overrides)
    return d


# ── analyze_price_path ───────────────────────────────────────────────────────

def test_price_path_gap_down():
    bars = _bars([
        (100.0, 102.0, 99.0, 101.0),
        (101.0, 103.0, 100.0, 102.0),
        (96.0, 97.0, 94.0, 94.5),   # opens -5.4% below prev close
    ])
    pp = analyze_price_path(bars, entry_price=100.0, stop_price=95.0, t1=110.0,
                            entry_date="2026-06-01", exit_date="2026-06-03")
    assert pp["descent_type"] == "GAP"
    assert pp["worst_gap_down_pct"] < -3.0
    assert pp["gap_bar_date"] == "2026-06-03"
    assert pp["hh_since_entry"] == 103.0
    assert 25.0 < pp["t1_progress_pct"] < 35.0
    assert pp["almost_worked"] is False
    assert pp["exit_open_below_stop"] is False
    assert pp["closed_below_entry_within_3_bars"] is True


def test_price_path_grind_down():
    bars = _bars([
        (100.0, 101.0, 99.0, 100.5),
        (100.4, 100.8, 98.5, 99.0),
        (99.0, 99.5, 97.0, 97.5),
        (97.4, 98.0, 95.5, 96.0),
        (96.0, 96.5, 94.0, 94.5),
    ])
    pp = analyze_price_path(bars, entry_price=100.0, stop_price=95.0, t1=110.0,
                            entry_date="2026-06-01", exit_date="2026-06-05")
    assert pp["descent_type"] == "GRIND"
    assert pp["gap_bar_date"] is None


def test_price_path_almost_worked():
    bars = _bars([
        (100.0, 104.0, 99.0, 103.0),
        (103.0, 108.0, 102.0, 107.0),  # HH 108 → 80% of the way to T1 110
        (106.0, 106.5, 98.0, 99.0),
        (99.0, 99.5, 94.0, 94.8),
    ])
    pp = analyze_price_path(bars, entry_price=100.0, stop_price=95.0, t1=110.0,
                            entry_date="2026-06-01", exit_date="2026-06-04")
    assert pp["hh_since_entry"] == 108.0
    assert pp["t1_progress_pct"] == 80.0
    assert pp["almost_worked"] is True


def test_price_path_exit_open_below_stop():
    bars = _bars([
        (100.0, 102.0, 99.0, 101.0),
        (93.0, 94.0, 91.0, 92.0),   # gaps straight through the stop
    ])
    pp = analyze_price_path(bars, entry_price=100.0, stop_price=95.0, t1=110.0,
                            entry_date="2026-06-01", exit_date="2026-06-02")
    assert pp["exit_open_below_stop"] is True
    assert pp["descent_type"] == "GAP"


# ── classify ─────────────────────────────────────────────────────────────────

def test_classify_earnings_primary():
    res = classify(_pp(), _cd(earnings_in_window=True, earnings_date="2026-06-02"), _row())
    assert res["primary"] == "EARNINGS_SURPRISE"
    assert "EARNINGS_SURPRISE" in res["evidence"]


def test_classify_gap_event():
    res = classify(_pp(descent_type="GAP", worst_gap_down_pct=-4.0), _cd(), _row())
    assert res["primary"] == "GAP_EVENT"


def test_classify_gap_event_via_exit_open_below_stop():
    res = classify(_pp(exit_open_below_stop=True), _cd(), _row())
    assert res["primary"] == "GAP_EVENT"


def test_classify_market_regime_flip_to_red():
    res = classify(_pp(), _cd(nifty_regime_entry="GREEN", nifty_regime_exit="RED"), _row())
    assert res["primary"] == "MARKET_REGIME_FLIP"


def test_classify_regime_downgrade_with_nifty_drop():
    res = classify(_pp(), _cd(nifty_regime_entry="GREEN", nifty_regime_exit="AMBER",
                              nifty_change_pct=-3.0), _row())
    assert res["primary"] == "MARKET_REGIME_FLIP"


def test_classify_regime_downgrade_without_drop_not_flip():
    res = classify(_pp(), _cd(nifty_regime_entry="GREEN", nifty_regime_exit="AMBER",
                              nifty_change_pct=-0.5), _row())
    assert res["primary"] != "MARKET_REGIME_FLIP"


def test_classify_sector_break():
    res = classify(_pp(), _cd(sector_pct_ema20_entry=-1.0, sector_pct_ema20_exit=-4.5), _row())
    assert res["primary"] == "SECTOR_BREAK"


def test_classify_false_breakout():
    res = classify(_pp(t1_progress_pct=10.0, closed_below_entry_within_3_bars=True), _cd(), _row())
    assert res["primary"] == "FALSE_BREAKOUT"


def test_classify_false_breakout_via_buy_risk():
    res = classify(_pp(t1_progress_pct=20.0), _cd(), _row(Buy_False_Breakout_Risk="HIGH"))
    assert res["primary"] == "FALSE_BREAKOUT"


def test_almost_worked_suppresses_false_breakout_adds_tight_sl():
    res = classify(_pp(t1_progress_pct=70.0, almost_worked=True,
                       closed_below_entry_within_3_bars=True), _cd(), _row())
    assert res["primary"] != "FALSE_BREAKOUT"
    assert "TIGHT_SL" in res["contributing"]


def test_classify_low_volume_contributing_only():
    res = classify(_pp(descent_type="GAP", worst_gap_down_pct=-4.0), _cd(),
                   _row(Buy_Vol_Ratio="0.4"))
    assert res["primary"] == "GAP_EVENT"
    assert "LOW_VOLUME_ENTRY" in res["contributing"]


def test_classify_low_volume_primary_when_alone():
    res = classify(_pp(), _cd(), _row(Buy_Vol_Ratio="0.4"))
    assert res["primary"] == "LOW_VOLUME_ENTRY"


def test_classify_tight_sl_heuristic():
    res = classify(_pp(), _cd(sl_distance_atr=0.6), _row())
    assert res["primary"] == "TIGHT_SL"


def test_classify_trailed_stop_negative_atr_not_tight_sl():
    # A stop trailed above entry (negative distance) is not a "tight initial SL"
    res = classify(_pp(), _cd(sl_distance_atr=-0.5), _row())
    assert res["primary"] == "UNKNOWN"


def test_classify_unknown():
    res = classify(_pp(), _cd(), _row())
    assert res["primary"] == "UNKNOWN"
    assert res["contributing"] == []


def test_classify_precedence_earnings_over_gap():
    res = classify(_pp(descent_type="GAP", worst_gap_down_pct=-4.0),
                   _cd(earnings_in_window=True, earnings_date="2026-06-02"), _row())
    assert res["primary"] == "EARNINGS_SURPRISE"
    assert "GAP_EVENT" in res["contributing"]
    assert "EARNINGS_SURPRISE" in res["evidence"] and "GAP_EVENT" in res["evidence"]


# ── derive_app_gaps ──────────────────────────────────────────────────────────

def _sector_rec(margin):
    return {
        "filter": "sector_nifty_regime", "gate": "Gate#9", "verdict": "PASS",
        "measured": {"sector": "PSU BANK", "pct_from_ema20": -2.0 + margin,
                     "sector_status": "AMBER", "nifty_regime": "GREEN",
                     "regime_mult": 0.75},
        "threshold": -2.0, "margin": margin,
    }


def test_app_gaps_marginal_pass_is_tighten():
    cls = {"primary": "SECTOR_BREAK", "contributing": [], "evidence": {"SECTOR_BREAK": "x"}}
    gaps = derive_app_gaps(cls, [_sector_rec(margin=0.5)])
    sector_gaps = [g for g in gaps if g["filter"] == "sector_nifty_regime"]
    assert len(sector_gaps) == 1
    assert sector_gaps[0]["type"] == "TIGHTEN"


def test_app_gaps_comfortable_pass_is_add():
    cls = {"primary": "SECTOR_BREAK", "contributing": [], "evidence": {"SECTOR_BREAK": "x"}}
    gaps = derive_app_gaps(cls, [_sector_rec(margin=6.0)])
    sector_gaps = [g for g in gaps if g["class"] == "SECTOR_BREAK"]
    assert len(sector_gaps) == 1
    assert sector_gaps[0]["type"] == "ADD"


def test_app_gaps_gap_event_always_add():
    cls = {"primary": "GAP_EVENT", "contributing": [], "evidence": {"GAP_EVENT": "x"}}
    gaps = derive_app_gaps(cls, [_sector_rec(margin=6.0)])
    gap_gaps = [g for g in gaps if g["class"] == "GAP_EVENT"]
    assert len(gap_gaps) == 1
    assert gap_gaps[0]["type"] == "ADD"
    assert gap_gaps[0]["filter"] != "none"  # readable label, not the literal fallback


def test_app_gaps_tight_sl_is_tighten_not_add():
    cls = {"primary": "TIGHT_SL", "contributing": [], "evidence": {"TIGHT_SL": "x"}}
    gaps = derive_app_gaps(cls, [_sector_rec(margin=6.0)])
    tight_gaps = [g for g in gaps if g["class"] == "TIGHT_SL"]
    assert len(tight_gaps) == 1
    assert tight_gaps[0]["type"] == "TIGHTEN"
    assert tight_gaps[0]["filter"] == "trade_plan_stop_sizing"


def test_app_gaps_partial_when_no_snapshot():
    cls = {"primary": "SECTOR_BREAK", "contributing": ["LOW_VOLUME_ENTRY"],
           "evidence": {"SECTOR_BREAK": "x", "LOW_VOLUME_ENTRY": "y"}}
    gaps = derive_app_gaps(cls, None)
    assert gaps, "expected gaps even without a snapshot"
    for g in gaps:
        assert g["detail"].startswith("(partial")


def test_app_gaps_unknown_produces_none():
    cls = {"primary": "UNKNOWN", "contributing": [], "evidence": {}}
    assert derive_app_gaps(cls, None) == []


# ── evaluate_tight_sl_recovery ───────────────────────────────────────────────

def test_recovery_strong():
    bars = _bars([
        (95.0, 97.0, 94.0, 96.0),
        (96.0, 102.0, 95.5, 101.0),   # back above entry 100
    ])
    res = evaluate_tight_sl_recovery(bars, entry_price=100.0, stop_price=95.0)
    assert res["recovered"] is True
    assert res["strength"] == "STRONG"
    assert res["recovery_close"] == 101.0


def test_recovery_weak():
    bars = _bars([
        (95.0, 96.0, 94.0, 95.5),
        (95.5, 98.0, 95.0, 97.5),     # above stop*1.02=96.9 but below entry
    ])
    res = evaluate_tight_sl_recovery(bars, entry_price=100.0, stop_price=95.0)
    assert res["recovered"] is True
    assert res["strength"] == "WEAK"


def test_recovery_none():
    bars = _bars([
        (95.0, 96.0, 93.0, 94.0),
        (94.0, 95.0, 92.0, 93.0),
    ])
    res = evaluate_tight_sl_recovery(bars, entry_price=100.0, stop_price=95.0)
    assert res["recovered"] is False
    assert res["strength"] is None


# ── regime / ema helpers ─────────────────────────────────────────────────────

def _index_frame(closes, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def test_regime_green_on_uptrend():
    closes = [100.0 + i for i in range(80)]
    df = _index_frame(closes)
    assert regime_from_frame(df, df.index[-1].strftime("%Y-%m-%d")) == "GREEN"


def test_regime_red_on_downtrend():
    closes = [180.0 - i for i in range(80)]
    df = _index_frame(closes)
    assert regime_from_frame(df, df.index[-1].strftime("%Y-%m-%d")) == "RED"


def test_regime_uses_only_data_up_to_as_of():
    # Uptrend then crash: as-of the peak it must still be GREEN.
    closes = [100.0 + i for i in range(60)] + [80.0 - i for i in range(20)]
    df = _index_frame(closes)
    peak_date = df.index[59].strftime("%Y-%m-%d")
    assert regime_from_frame(df, peak_date) == "GREEN"
    assert regime_from_frame(df, df.index[-1].strftime("%Y-%m-%d")) == "RED"


def test_ema20_pct_flat_is_zero():
    df = _index_frame([100.0] * 40)
    pct = ema20_from_frame(df, df.index[-1].strftime("%Y-%m-%d"))
    assert abs(pct) < 0.5


def test_ema20_pct_positive_on_uptrend():
    df = _index_frame([100.0 + i for i in range(40)])
    pct = ema20_from_frame(df, df.index[-1].strftime("%Y-%m-%d"))
    assert pct > 0


# ── weekly digest ────────────────────────────────────────────────────────────

def test_digest_none_when_empty():
    agg = {"by_class": {}, "total": 0, "partial_count": 0, "gaps": [],
           "pending_rechecks": 0, "tight_sl_upgrades": 0}
    assert build_weekly_digest_text(agg, []) is None


def test_digest_renders_html():
    agg = {"by_class": {"GAP_EVENT": 3, "LOW_VOLUME_ENTRY": 2}, "total": 5,
           "partial_count": 4,
           "gaps": [{"filter": "sector_nifty_regime", "type": "TIGHTEN", "count": 2}],
           "pending_rechecks": 1, "tight_sl_upgrades": 0}
    week = [{"symbol": "JSFB", "primary": "GAP_EVENT", "exit_date": "2026-07-07"}]
    text = build_weekly_digest_text(agg, week)
    assert isinstance(text, str)
    assert "<b>" in text
    assert "JSFB" in text
    assert "GAP_EVENT" in text
