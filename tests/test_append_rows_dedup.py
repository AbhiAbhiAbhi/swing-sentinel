"""Tests for _append_rows_to_csv dedup guards (issue #8).

Covers the new status-independent (Symbol, Entry_Date) stale-checkout guard
plus regression coverage of the existing Symbol-level blocks.
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from server import _append_rows_to_csv


TODAY = date.today()
OLD_ENTRY = (TODAY - timedelta(days=40)).isoformat()   # past every cooldown
OLD_EXIT = (TODAY - timedelta(days=30)).isoformat()


def _new_row(symbol="TEST", entry_date=None, **overrides):
    d = {
        "Symbol": symbol,
        "Entry_Date": entry_date or TODAY.isoformat(),
        "Status": "OPEN",
        "Entry_Price": 100.0,
        "Current_SL": 95.0,
        "Quantity": 10,
    }
    d.update(overrides)
    return d


def _seed_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_append_to_missing_file_creates_and_locks_risk(tmp_path):
    path = str(tmp_path / "positions.csv")
    added, skipped = _append_rows_to_csv(path, [_new_row("ABC")])
    assert [r["Symbol"] for r in added] == ["ABC"]
    assert skipped == []
    assert os.path.exists(path)
    df = pd.read_csv(path)
    assert len(df) == 1
    assert "Risk_Per_Share" in df.columns
    assert "Rupee_Risk" in df.columns


def test_duplicate_symbol_entry_date_blocked_regardless_of_status(tmp_path):
    """Stale-checkout guard: same Symbol + Entry_Date as an existing row is
    skipped even when the existing row is CLOSED and past every cooldown."""
    path = str(tmp_path / "positions.csv")
    _seed_csv(path, [{
        "Symbol": "IFCI",
        "Entry_Date": OLD_ENTRY,
        "Status": "CLOSED",
        "T2_Hit_Date": OLD_EXIT,
    }])
    added, skipped = _append_rows_to_csv(path, [_new_row("IFCI", entry_date=OLD_ENTRY)])
    assert added == []
    assert skipped == ["IFCI"]
    assert len(pd.read_csv(path)) == 1


def test_same_symbol_different_entry_date_allowed(tmp_path):
    """Guard must not over-block: re-entry with a new Entry_Date after all
    cooldowns have lapsed is legitimate."""
    path = str(tmp_path / "positions.csv")
    _seed_csv(path, [{
        "Symbol": "IFCI",
        "Entry_Date": OLD_ENTRY,
        "Status": "CLOSED",
        "T2_Hit_Date": OLD_EXIT,
    }])
    added, skipped = _append_rows_to_csv(path, [_new_row("IFCI")])
    assert [r["Symbol"] for r in added] == ["IFCI"]
    assert skipped == []
    assert len(pd.read_csv(path)) == 2


def test_active_open_position_still_blocks(tmp_path):
    path = str(tmp_path / "positions.csv")
    _seed_csv(path, [{
        "Symbol": "MOSCHIP",
        "Entry_Date": OLD_ENTRY,
        "Status": "OPEN",
    }])
    added, skipped = _append_rows_to_csv(path, [_new_row("MOSCHIP")])
    assert added == []
    assert skipped == ["MOSCHIP"]


def test_intra_batch_duplicate_pair_deduped(tmp_path):
    path = str(tmp_path / "positions.csv")
    rows = [_new_row("NITCO"), _new_row("NITCO")]
    added, skipped = _append_rows_to_csv(path, rows)
    assert len(added) == 1
    assert skipped == ["NITCO"]
    assert len(pd.read_csv(path)) == 1
