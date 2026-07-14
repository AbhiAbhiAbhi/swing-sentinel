"""
Offline unit tests for the ARMED ENTRY READY trigger (issue #9) — pure helper
_armed_entry_hit in core/server.py, no Flask, no yfinance, no file I/O.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from server import _armed_entry_hit  # noqa: E402


# ── Pullback / non-breakout setups ───────────────────────────────────────────

def test_pullback_in_zone_fires():
    assert _armed_entry_hit("PULLBACK", cur=100.0, ep=100.0, sl=95.0,
                            entry_min=0.0, prev=0.0, price_fresh=True)


def test_pullback_below_sl_never_fires():
    # Gap down through the entry zone below the planned SL: invalidated setup.
    assert not _armed_entry_hit("PULLBACK", cur=94.0, ep=100.0, sl=95.0,
                                entry_min=0.0, prev=0.0, price_fresh=True)


def test_pullback_no_sl_uses_entry_min_floor():
    assert not _armed_entry_hit("PULLBACK", cur=90.0, ep=100.0, sl=0.0,
                                entry_min=96.0, prev=0.0, price_fresh=True)
    assert _armed_entry_hit("PULLBACK", cur=96.0, ep=100.0, sl=0.0,
                            entry_min=96.0, prev=0.0, price_fresh=True)


def test_pullback_no_floor_data_still_fires_in_zone():
    assert _armed_entry_hit("PULLBACK", cur=99.0, ep=100.0, sl=0.0,
                            entry_min=0.0, prev=0.0, price_fresh=True)


def test_stale_price_never_fires():
    assert not _armed_entry_hit("PULLBACK", cur=100.0, ep=100.0, sl=95.0,
                                entry_min=0.0, prev=0.0, price_fresh=False)


# ── Breakout setups: require an upward cross ────────────────────────────────

def test_breakout_upward_cross_fires():
    assert _armed_entry_hit("BREAKOUT", cur=101.0, ep=100.0, sl=95.0,
                            entry_min=0.0, prev=99.0, price_fresh=True)


def test_breakout_falling_back_to_level_does_not_fire():
    # Prior poll above the level, price falling back down: failed breakout.
    assert not _armed_entry_hit("BREAKOUT", cur=100.2, ep=100.0, sl=95.0,
                                entry_min=0.0, prev=103.0, price_fresh=True)


def test_breakout_first_poll_no_prev_does_not_fire():
    assert not _armed_entry_hit("BREAKOUT", cur=101.0, ep=100.0, sl=95.0,
                                entry_min=0.0, prev=0.0, price_fresh=True)


def test_breakout_below_level_does_not_fire():
    assert not _armed_entry_hit("BREAKOUT", cur=99.0, ep=100.0, sl=95.0,
                                entry_min=0.0, prev=98.0, price_fresh=True)


def test_breakout_cross_below_floor_does_not_fire():
    # Cross of the level but still under the SL floor (bad/misconfigured row).
    assert not _armed_entry_hit("BREAKOUT", cur=101.0, ep=100.0, sl=102.0,
                                entry_min=0.0, prev=99.0, price_fresh=True)


def test_setup_type_case_insensitive():
    assert not _armed_entry_hit(" breakout ", cur=100.0, ep=100.0, sl=0.0,
                                entry_min=0.0, prev=0.0, price_fresh=True)
