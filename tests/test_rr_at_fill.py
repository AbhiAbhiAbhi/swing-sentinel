"""
Offline unit tests for the fill-time minimum R:R gate (issue #10) — pure helper
_rr_at_fill in core/server.py, no Flask, no yfinance, no file I/O.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from server import _rr_at_fill  # noqa: E402


def test_healthy_trade_rr_2():
    assert _rr_at_fill(100.0, 95.0, 110.0) == 2.0


def test_chased_fill_collapses_rr():
    # Fill chased from 100 to 105 with original SL/T2 → R:R 0.5, below 1.5
    rr = _rr_at_fill(105.0, 95.0, 110.0)
    assert rr == 0.5
    assert rr < 1.5


def test_sl_at_entry_returns_none():
    assert _rr_at_fill(100.0, 100.0, 110.0) is None


def test_sl_above_entry_returns_none():
    assert _rr_at_fill(100.0, 102.0, 110.0) is None


def test_rr_exactly_at_threshold_passes():
    # Gate blocks only when rr < min_rr, so exactly 1.5 passes
    rr = _rr_at_fill(100.0, 96.0, 106.0)
    assert rr == 1.5
    assert not (rr < 1.5)


def test_t2_below_entry_gives_negative_rr():
    assert _rr_at_fill(100.0, 95.0, 98.0) < 0
