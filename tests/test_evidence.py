"""Unit tests for the historical & live evidence engine.

All tests run offline on synthetic OHLCV frames — no yfinance, no Flask.
Zero-cost/zero-slippage rules make R math exact where precision matters.
"""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

import core_evidence as ev
import core_evidence_store as store


# ── helpers ──────────────────────────────────────────────────────────────────

def make_df(bars):
    """bars: list of (open, high, low, close) → daily OHLCV DataFrame."""
    idx = pd.bdate_range("2025-01-01", periods=len(bars))
    o, h, l, c = zip(*bars)
    return pd.DataFrame(
        {"Open": o, "High": h, "Low": l, "Close": c,
         "Volume": [1_000_000] * len(bars)}, index=idx)


def flat_bars(n, px=100.0):
    """Quiet bars that never touch stops or targets in the tests below."""
    return [(px, px + 1, px - 1, px)] * n


def zero_cost_rules():
    r = json.loads(json.dumps(ev.DEFAULT_RULES))
    r["cost_model"] = {k: 0.0 for k in r["cost_model"]}
    return r


PLAN = {
    "setup_type": "PULLBACK",
    "entry_zone_min": 99.0, "entry_zone_max": 100.0,
    "stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0,
}


def run_ep(bars, signal_idx=0, plan=None, rules=None):
    return ev.simulate_episode(make_df(bars), signal_idx, plan or PLAN,
                               rules or zero_cost_rules())


# ── simulate_episode ─────────────────────────────────────────────────────────

def test_not_filled_when_limit_never_touched():
    # Lows stay above the 100 limit for all 5 wait sessions
    bars = [(105, 106, 104, 105)] * 8
    ep = run_ep(bars)
    assert ep["fill_status"] == "NOT_FILLED"
    assert ep["exits"] == []
    assert ep["net_r"] is None
    assert ep["end_idx"] == 5  # signal + max_wait


def test_fill_at_limit_then_gap_through_stop():
    bars = [
        (105, 106, 104, 105),      # signal bar
        (101, 102, 100, 101),      # low touches limit → fill at 100
        (90, 91, 88, 89),          # opens at 90, below the 95 stop → gap exit
    ] + flat_bars(30)
    ep = run_ep(bars)
    assert ep["fill_status"] == "FILLED"
    assert ep["fill_price"] == 100.0
    assert ep["exits"][0]["reason"] == "SL"
    assert ep["exits"][0]["price"] == 90.0    # open, NOT the ideal stop
    assert ep["gap_through_stop"] is True
    assert ep["net_r"] == -2.0                # lost 10 on a 5 risk


def test_gap_down_open_inside_zone_fills_at_open():
    bars = [
        (105, 106, 104, 105),
        (98, 99, 97, 98),          # gaps below the limit → fill at open 98
    ] + flat_bars(30, px=100)
    ep = run_ep(bars)
    assert ep["fill_price"] == 98.0


def test_same_bar_stop_and_target_is_stop_first():
    bars = [
        (105, 106, 104, 105),
        (100, 100, 100, 100),      # fill at 100
        (100, 121, 94, 100),       # touches both T2 (120) and SL (95)
    ] + flat_bars(30)
    ep = run_ep(bars)
    assert [e["reason"] for e in ep["exits"]] == ["SL"]
    assert ep["exits"][0]["price"] == 95.0
    assert ep["net_r"] == -1.0


def test_t1_partial_then_breakeven_exit():
    bars = [
        (105, 106, 104, 105),
        (100, 100, 100, 100),          # fill at 100
        (105, 111, 104, 108),          # T1 110 hit → 50% out, stop → 100
        (101, 102, 98, 99),            # low 98 <= breakeven stop 100 → rest out
    ] + flat_bars(30)
    ep = run_ep(bars)
    reasons = [e["reason"] for e in ep["exits"]]
    assert reasons == ["T1", "BREAKEVEN"]
    assert ep["exits"][0]["fraction"] == 0.5
    # 0.5 × (110-100)/(100-95) = +1.0R; breakeven leg contributes ~0
    assert ep["net_r"] == pytest.approx(1.0, abs=0.02)
    assert ep["gap_through_stop"] is False    # breakeven gap is not a stop gap


def test_t1_then_t2_full_sequence():
    bars = [
        (105, 106, 104, 105),
        (100, 100, 100, 100),          # fill 100
        (105, 111, 104, 108),          # T1
        (112, 121, 111, 119),          # T2
    ] + flat_bars(30)
    ep = run_ep(bars)
    assert [e["reason"] for e in ep["exits"]] == ["T1", "T2"]
    # 0.5×10/5 + 0.5×20/5 = 1 + 2 = 3R
    assert ep["net_r"] == pytest.approx(3.0, abs=0.02)


def test_time_exit_after_max_hold():
    bars = [(105, 106, 104, 105), (100, 100, 100, 100)] + \
        [(102, 103, 101, 102)] * 25   # never touches 95 / 110
    ep = run_ep(bars)
    assert ep["exits"][-1]["reason"] == "TIME"
    assert ep["hold_sessions"] == 20


def test_costs_reduce_net_r():
    rules = json.loads(json.dumps(ev.DEFAULT_RULES))  # real india-cash-v1
    bars = [
        (105, 106, 104, 105),
        (100, 100, 100, 100),
        (100, 121, 100, 120),          # straight to T2
    ] + flat_bars(30)
    ep = run_ep(bars, rules=rules)
    assert ep["gross_r"] is not None and ep["net_r"] is not None
    assert ep["net_r"] < ep["gross_r"]


# ── episode de-duplication ───────────────────────────────────────────────────

def _plan_fn(df, i):
    return dict(PLAN)


def test_episode_dedup_and_cooldown():
    # 80 quiet bars; injected NOT_FILLED signals: end_idx = i+5, cooldown 5
    df = make_df([(105, 106, 104, 105)] * 80)
    rules = zero_cost_rules()
    episodes, observed = ev.build_episodes(
        df, "PULLBACK", rules, 0,
        signal_indices=[10, 12, 20, 21, 30], plan_fn=_plan_fn)
    # 10 blocks until 10+5+5=20 inclusive → 12 and 20 suppressed; 21 opens
    # (blocks until 31) → 30 suppressed.
    assert observed == 5
    assert len(episodes) == 2
    assert [e["signal_date"] for e in episodes] == [
        df.index[10].strftime("%Y-%m-%d"), df.index[21].strftime("%Y-%m-%d")]


def test_signals_too_close_to_data_end_are_dropped():
    df = make_df([(105, 106, 104, 105)] * 40)
    rules = zero_cost_rules()   # wait 5 + hold 20 + 1 → last valid idx 13
    episodes, observed = ev.build_episodes(
        df, "PULLBACK", rules, 0, signal_indices=[13, 14, 30], plan_fn=_plan_fn)
    assert observed == 1
    assert len(episodes) == 1


# ── coverage extension & labels ──────────────────────────────────────────────

def test_extends_to_24_months_when_sample_small(monkeypatch):
    calls = []

    def fake_build(df, setup, rules, window_start, filters=None, **kw):
        calls.append(window_start)
        n = 3 if len(calls) == 1 else 20
        return [{"fill_status": "NOT_FILLED", "end_idx": 0, "net_r": None,
                 "hold_sessions": None, "gap_through_stop": False}] * n, n

    monkeypatch.setattr(ev, "build_episodes", fake_build)
    df = make_df([(100, 101, 99, 100)] * 800)
    out = ev.run_historical_evidence("TEST", "PULLBACK", df=df,
                                     rules=ev.load_rule_config())
    assert len(calls) == 2
    assert calls[1] < calls[0]                      # wider window on retry
    assert out["coverage"]["months"] == 24
    assert out["summary"]["independent_episodes"] == 20
    assert out["coverage"]["sample_quality"] == "usable"


def test_short_history_returns_insufficient_not_crash():
    df = make_df([(100, 101, 99, 100)] * 80)   # < 220 bars (recent IPO)
    out = ev.run_historical_evidence("NEWIPO", "PULLBACK", df=df,
                                     rules=ev.load_rule_config())
    assert out["status"] == "insufficient_history"
    assert out["coverage"]["sample_quality"] == "insufficient"
    assert out["episodes"] == []


def test_sample_labels():
    assert ev.sample_label(0) == "insufficient"
    assert ev.sample_label(4) == "insufficient"
    assert ev.sample_label(5) == "weak"
    assert ev.sample_label(14) == "weak"
    assert ev.sample_label(15) == "usable"
    assert ev.sample_label(29) == "usable"
    assert ev.sample_label(30) == "stronger"


# ── strategy version ─────────────────────────────────────────────────────────

def test_strategy_version_deterministic_and_sensitive():
    cfg1 = ev.load_rule_config()
    cfg2 = json.loads(json.dumps(cfg1))
    assert ev.strategy_version(cfg1) == ev.strategy_version(cfg2)
    assert ev.strategy_version(cfg1).startswith("v1.0-")
    cfg2["max_hold_sessions"] = 25
    assert ev.strategy_version(cfg1) != ev.strategy_version(cfg2)


# ── store ────────────────────────────────────────────────────────────────────

def _result(**over):
    d = {"schema_version": 1, "symbol": "ABC", "setup_type": "PULLBACK",
         "strategy_version": "v1.0-deadbeef0000",
         "market_data_as_of": "2026-07-10",
         "summary": {"wins": 2}, "episodes": [], "status": "complete",
         "stale_reason": None}
    d.update(over)
    return d


def test_store_roundtrip_atomic(tmp_path):
    root = str(tmp_path)
    path = store.write_cache(_result(), root=root)
    assert os.path.exists(path) and not os.path.exists(path + ".tmp")
    back = store.read_cache("ABC", "PULLBACK", "v1.0-deadbeef0000", root=root)
    assert back["summary"]["wins"] == 2


def test_store_stale_detection():
    from datetime import datetime
    now = datetime(2026, 7, 10, 18, 0)   # Friday evening
    stale, why = store.is_stale(_result(market_data_as_of="2026-07-09"), now=now)
    assert stale
    fresh, _ = store.is_stale(_result(market_data_as_of="2026-07-10"), now=now)
    assert not fresh
    # Saturday: last completed session is still Friday
    sat = datetime(2026, 7, 11, 12, 0)
    fresh2, _ = store.is_stale(_result(market_data_as_of="2026-07-10"), now=sat)
    assert not fresh2


def test_mark_error_preserves_last_good(tmp_path):
    root = str(tmp_path)
    store.write_cache(_result(), root=root)
    out = store.mark_error("ABC", "PULLBACK", "v1.0-deadbeef0000",
                           "yfinance timed out", root=root)
    assert out["status"] == "error_stale"
    assert out["summary"]["wins"] == 2      # last good summary intact
    assert "yfinance" in out["stale_reason"]


def test_mark_error_without_prior_cache_writes_stub(tmp_path):
    root = str(tmp_path)
    out = store.mark_error("XYZ", "BREAKOUT", "v1.0-cafebabe0000", "boom", root=root)
    assert out["status"] == "error"
    assert store.read_cache("XYZ", "BREAKOUT", "v1.0-cafebabe0000", root=root)


# ── live evidence ────────────────────────────────────────────────────────────

def _row(**over):
    d = {"Symbol": "ABC", "Setup": "PULLBACK", "Entry_Price": "100.0",
         "Closing_Price": "110.0", "Initial_SL": "95.0", "Buy_RSI": "55",
         "Current_SL": "", "Outcome": "T2_WIN", "T1_Hit_Date": "",
         "Risk_Per_Share": "", "Entry_Date": "2026-06-01",
         "T2_Hit_Date": "2026-06-10", "SL_Hit_Date": ""}
    d.update(over)
    return d


def test_live_evidence_basic():
    rows = [_row(), _row(Closing_Price="95.0", Outcome="SL_LOSS",
                         T2_Hit_Date="", SL_Hit_Date="2026-06-05")]
    out = ev.build_live_evidence("ABC", "PULLBACK", rows, {}, "v1")
    s = out["summary"]
    assert s["completed"] == 2
    assert s["complete_snapshots"] == 2
    assert s["net_expectancy_r"] == pytest.approx(0.5)  # mean(+2R, -1R)
    assert s["win_rate"] == 0.5
    assert out["status"] == "low_confidence"


def test_live_evidence_legacy_row_counts_partial_and_no_r_excluded():
    rows = [
        _row(),
        _row(Initial_SL="", Buy_RSI="", Outcome="T2_WIN"),  # unrecoverable SL
    ]
    out = ev.build_live_evidence("ABC", "PULLBACK", rows, {}, "v1")
    s = out["summary"]
    assert s["completed"] == 2
    assert s["complete_snapshots"] == 1
    assert s["partial_legacy_records"] == 1
    assert s["r_sample"] == 1               # no-R trade excluded, not estimated
    assert s["net_expectancy_r"] == pytest.approx(2.0)


def test_live_evidence_filters_symbol_and_setup():
    rows = [_row(), _row(Symbol="OTHER"), _row(Setup="BREAKOUT")]
    out = ev.build_live_evidence("ABC", "PULLBACK", rows, {}, "v1")
    assert out["summary"]["completed"] == 1


def test_live_evidence_no_data():
    out = ev.build_live_evidence("ABC", "PULLBACK", [], {}, "v1")
    assert out["status"] == "no_data"
    assert out["summary"]["completed"] == 0
