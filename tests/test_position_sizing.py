"""Unit tests for compute_position_size (issue #7 position sizing).

All tests run offline on pure inputs — no Flask, no file I/O.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from core_trade_plan import compute_position_size, position_risk


# ── normal sizing math ───────────────────────────────────────────────────────

def test_full_size_basic():
    # ₹10,000 @ 1% = ₹100 budget; risk/share ₹5 → 20 shares
    res = compute_position_size(100.0, 95.0, 10000, 1.0)
    assert res["quantity"] == 20
    assert res["risk_per_share"] == 5.0
    assert res["combined_mult"] == 1.0
    assert res["rupee_risk_budget"] == 100.0
    assert res["reason"] == ""


def test_floor_behavior():
    # budget ₹100, risk/share ₹3 → floor(33.33) = 33
    res = compute_position_size(100.0, 97.0, 10000, 1.0)
    assert res["quantity"] == 33


def test_half_size_expiry():
    res = compute_position_size(100.0, 95.0, 10000, 1.0, expiry_mult=0.5)
    assert res["quantity"] == 10
    assert res["combined_mult"] == 0.5


def test_quarter_size_expiry():
    res = compute_position_size(100.0, 95.0, 10000, 1.0, expiry_mult=0.25)
    assert res["quantity"] == 5


def test_multipliers_combine():
    # 0.5 expiry × 0.5 regime = 0.25× → 5 shares
    res = compute_position_size(100.0, 95.0, 10000, 1.0,
                                expiry_mult=0.5, regime_mult=0.5)
    assert res["quantity"] == 5
    assert res["combined_mult"] == 0.25


# ── zero / blocked cases ─────────────────────────────────────────────────────

def test_zero_expiry_mult_blocks():
    res = compute_position_size(100.0, 95.0, 10000, 1.0, expiry_mult=0.0)
    assert res["quantity"] == 0
    assert "multiplier is 0" in res["reason"]


def test_zero_regime_mult_blocks():
    res = compute_position_size(100.0, 95.0, 10000, 1.0, regime_mult=0.0)
    assert res["quantity"] == 0
    assert "multiplier is 0" in res["reason"]


def test_capital_too_small():
    # budget ₹100, risk/share ₹150 → 0 with a capital reason
    res = compute_position_size(1000.0, 850.0, 10000, 1.0)
    assert res["quantity"] == 0
    assert "capital too small" in res["reason"]


def test_invalid_risk_per_share():
    # SL above entry → invalid
    res = compute_position_size(100.0, 105.0, 10000, 1.0)
    assert res["quantity"] == 0
    assert "invalid risk_per_share" in res["reason"]


def test_missing_entry_or_sl():
    assert compute_position_size("", 95.0, 10000, 1.0)["quantity"] == 0
    assert compute_position_size(100.0, None, 10000, 1.0)["quantity"] == 0


# ── blank multiplier defaults ────────────────────────────────────────────────

def test_blank_regime_mult_defaults_to_1():
    res = compute_position_size(100.0, 95.0, 10000, 1.0,
                                expiry_mult=1.0, regime_mult="")
    assert res["quantity"] == 20
    assert res["combined_mult"] == 1.0


def test_garbage_mult_defaults_to_1():
    res = compute_position_size(100.0, 95.0, 10000, 1.0,
                                expiry_mult="nan-ish", regime_mult=None)
    assert res["quantity"] == 20


# ── string inputs (CSV round-trip) ───────────────────────────────────────────

def test_string_inputs():
    res = compute_position_size("100.0", "95.0", "10000", "1.0",
                                expiry_mult="0.5", regime_mult="1.0")
    assert res["quantity"] == 10


# ── downstream rupee risk consistency ────────────────────────────────────────

def test_position_risk_uses_sized_quantity():
    res = compute_position_size(100.0, 95.0, 10000, 1.0)
    rps, rupee = position_risk(100.0, 95.0, res["quantity"])
    assert rps == 5.0
    assert rupee == 100.0  # sized ₹ risk equals the full budget here
