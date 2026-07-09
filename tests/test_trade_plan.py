"""Invariant tests for calculate_trade_plan — targets must sit above the
worst-case fill (entry_zone_max) and SL below the entry zone on fresh entries."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from core_trade_plan import calculate_trade_plan


def _base(**overrides):
    data = {
        "price": 100.0,
        "ema20": 98.0,
        "ema50": 95.0,
        "support_1": 92.0,
        "resistance_1": 104.0,
        "resistance_2": 112.0,
        "atr": 2.0,
        "rsi": 55,
    }
    data.update(overrides)
    return data


def _assert_invariants(plan):
    assert plan["target_1"] > plan["entry_zone_max"], plan
    assert plan["target_2"] > plan["target_1"], plan
    assert plan["stop_loss"] < plan["entry_zone_min"], plan


def test_breakout_price_at_resistance():
    # The exact case that produced fake wins: price >= resistance_1, entry zone
    # chases to resistance_1 * 1.02, old T1 pinned at resistance_1 (below fill).
    plan = calculate_trade_plan(_base(price=105.0, resistance_1=104.0))
    assert plan["setup_type"] == "BREAKOUT"
    _assert_invariants(plan)


def test_breakout_price_far_above_resistance():
    plan = calculate_trade_plan(_base(price=110.0, resistance_1=104.0, resistance_2=111.0))
    assert plan["setup_type"] == "BREAKOUT"
    _assert_invariants(plan)


def test_support_bounce():
    plan = calculate_trade_plan(_base(price=93.0, resistance_1=0, support_1=92.0))
    assert plan["setup_type"] == "SUPPORT_BOUNCE"
    _assert_invariants(plan)


def test_pullback():
    plan = calculate_trade_plan(_base(price=98.5, resistance_1=110.0, support_1=90.0, rsi=50))
    assert plan["setup_type"] == "PULLBACK"
    _assert_invariants(plan)


def test_consolidation():
    plan = calculate_trade_plan(_base(price=105.0, resistance_1=110.0, support_1=90.0, rsi=70,
                                      ema20=101.0, ema50=96.0))
    assert plan["setup_type"] == "CONSOLIDATION"
    _assert_invariants(plan)


def test_resistance_above_entry_zone_is_kept_as_t1():
    # Resistance well above the entry zone should still be the T1 anchor.
    plan = calculate_trade_plan(_base(price=98.5, resistance_1=110.0, support_1=90.0, rsi=50))
    assert plan["target_1"] == 110.0


def test_missing_atr_falls_back():
    plan = calculate_trade_plan(_base(price=105.0, resistance_1=104.0, atr=None))
    _assert_invariants(plan)
