"""Automated SL post-mortem engine (issue #3).

When a position exits at its stop-loss (Outcome=SL_LOSS), this module
reconstructs the entry→stop price path, diffs entry vs exit market
conditions, classifies the failure into a fixed deterministic taxonomy,
and explains whether an existing entry filter could have caught it
(TIGHTEN) or no filter covers that failure mode (ADD).

Classification is fully deterministic — the optional LLM narrative only
rephrases the classified result and never feeds back into it (same
principle as llm_signal.py: LLM interprets, never generates numbers).

Results live in sidecar JSONs at data/post_mortems/{SYMBOL}_{entry_date}.json;
positions.csv only carries small denormalized string columns for the dashboard.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env if present (python-dotenv optional — falls back gracefully; on
# GitHub Actions env vars come from secrets directly, no .env file exists)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
except ImportError:
    pass

# ── Taxonomy ─────────────────────────────────────────────────────────────────
# Precedence order for the primary class: first match wins, every match is
# recorded in `contributing`.
CLASS_EARNINGS_SURPRISE = "EARNINGS_SURPRISE"
CLASS_GAP_EVENT = "GAP_EVENT"
CLASS_MARKET_REGIME_FLIP = "MARKET_REGIME_FLIP"
CLASS_SECTOR_BREAK = "SECTOR_BREAK"
CLASS_FALSE_BREAKOUT = "FALSE_BREAKOUT"
CLASS_LOW_VOLUME_ENTRY = "LOW_VOLUME_ENTRY"
CLASS_TIGHT_SL = "TIGHT_SL"
CLASS_UNKNOWN = "UNKNOWN"

PRECEDENCE = [
    CLASS_EARNINGS_SURPRISE,
    CLASS_GAP_EVENT,
    CLASS_MARKET_REGIME_FLIP,
    CLASS_SECTOR_BREAK,
    CLASS_FALSE_BREAKOUT,
    CLASS_LOW_VOLUME_ENTRY,
    CLASS_TIGHT_SL,
]

# Thresholds (module constants so tests and docs reference one place)
GAP_DOWN_PCT = -3.0            # open ≤ prev close × 0.97 counts as a gap event
NIFTY_DROP_PCT = -2.0          # regime downgrade needs this much Nifty damage
SECTOR_EMA20_RED = -2.0        # Gate #9 semantics: below this = sector RED
FALSE_BREAKOUT_MAX_PROGRESS = 33.0
ALMOST_WORKED_PROGRESS = 66.0
LOW_VOLUME_RATIO = 1.0
TIGHT_SL_ATR_RATIO = 1.0       # stop closer than 1×ATR = tight
RECOVERY_WEAK_STOP_MULT = 1.02
MARGINAL_PASS_FRACTION = 0.15  # |margin| ≤ 15% of |threshold| = passed marginally
SECTOR_MARGIN_PTS = 1.0        # sector gate margin is in pct-points, not a ratio

_REGIME_RANK = {"GREEN": 2, "AMBER": 1, "RED": 0}


def _f(value):
    """Best-effort float coercion for CSV string fields; None when absent."""
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        v = float(value)
        return v if v == v else None  # NaN guard
    except (TypeError, ValueError):
        return None


# ── Price path ───────────────────────────────────────────────────────────────

def analyze_price_path(bars, entry_price, stop_price, t1,
                       entry_date, exit_date):
    """Reconstruct the entry→stop journey from daily OHLCV bars.

    `bars` must already be sliced to [entry_date, exit_date].
    """
    entry_price = _f(entry_price)
    stop_price = _f(stop_price)
    t1 = _f(t1)

    closes = bars["Close"]
    opens = bars["Open"]
    highs = bars["High"]

    worst_gap = None
    gap_bar_date = None
    for i in range(1, len(bars)):
        prev_close = float(closes.iloc[i - 1])
        if prev_close <= 0:
            continue
        gap_pct = (float(opens.iloc[i]) - prev_close) / prev_close * 100.0
        if worst_gap is None or gap_pct < worst_gap:
            worst_gap = gap_pct
            if gap_pct <= GAP_DOWN_PCT:
                gap_bar_date = bars.index[i].strftime("%Y-%m-%d")

    descent_type = "GAP" if (worst_gap is not None and worst_gap <= GAP_DOWN_PCT) else "GRIND"
    if descent_type == "GRIND":
        gap_bar_date = None

    hh = float(highs.max()) if len(highs) else None
    t1_progress = None
    if hh is not None and entry_price and t1 and t1 > entry_price:
        t1_progress = max(0.0, (hh - entry_price) / (t1 - entry_price) * 100.0)

    exit_open_below_stop = bool(
        stop_price is not None and len(opens) and float(opens.iloc[-1]) < stop_price
    )

    closed_below_entry_early = bool(
        entry_price is not None
        and any(float(c) < entry_price for c in closes.iloc[:3])
    )

    try:
        days_held = (pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days
    except (ValueError, TypeError):
        days_held = None

    exit_price = float(closes.iloc[-1]) if len(closes) else None

    return {
        "entry_price": entry_price,
        "stop_price": stop_price,
        "exit_price": exit_price,
        "t1": t1,
        "days_held": days_held,
        "bars": int(len(bars)),
        "descent_type": descent_type,
        "worst_gap_down_pct": round(worst_gap, 2) if worst_gap is not None else None,
        "gap_bar_date": gap_bar_date,
        "hh_since_entry": hh,
        "t1_progress_pct": round(t1_progress, 1) if t1_progress is not None else None,
        "almost_worked": bool(t1_progress is not None and t1_progress >= ALMOST_WORKED_PROGRESS),
        "exit_open_below_stop": exit_open_below_stop,
        "closed_below_entry_within_3_bars": closed_below_entry_early,
    }


# ── Classification ───────────────────────────────────────────────────────────

def classify(price_path, condition_diff, row):
    """Deterministic taxonomy classification.

    Returns {"primary": CLASS, "contributing": [CLASS...], "evidence": {CLASS: reason}}.
    """
    pp, cd = price_path, condition_diff
    evidence = {}

    if cd.get("earnings_in_window"):
        evidence[CLASS_EARNINGS_SURPRISE] = (
            f"earnings on {cd.get('earnings_date') or '?'} fell inside the hold window"
        )

    gap = pp.get("worst_gap_down_pct")
    if (pp.get("descent_type") == "GAP" and gap is not None and gap <= GAP_DOWN_PCT) \
            or pp.get("exit_open_below_stop"):
        if pp.get("exit_open_below_stop"):
            evidence[CLASS_GAP_EVENT] = "exit day opened below the stop — stopped on the gap itself"
        else:
            evidence[CLASS_GAP_EVENT] = (
                f"gapped down {gap:.1f}% on {pp.get('gap_bar_date') or '?'}"
            )

    re_entry = _REGIME_RANK.get(str(cd.get("nifty_regime_entry") or "").upper())
    re_exit = _REGIME_RANK.get(str(cd.get("nifty_regime_exit") or "").upper())
    nifty_chg = _f(cd.get("nifty_change_pct"))
    if re_entry is not None and re_exit is not None and re_exit < re_entry:
        flipped_to_red = re_exit == _REGIME_RANK["RED"] and re_entry > _REGIME_RANK["RED"]
        downgraded_with_drop = nifty_chg is not None and nifty_chg <= NIFTY_DROP_PCT
        if flipped_to_red or downgraded_with_drop:
            evidence[CLASS_MARKET_REGIME_FLIP] = (
                f"Nifty regime {cd.get('nifty_regime_entry')} → {cd.get('nifty_regime_exit')}"
                + (f" with Nifty {nifty_chg:+.1f}% over the hold" if nifty_chg is not None else "")
            )

    sec_entry = _f(cd.get("sector_pct_ema20_entry"))
    sec_exit = _f(cd.get("sector_pct_ema20_exit"))
    if sec_entry is not None and sec_exit is not None \
            and sec_entry >= SECTOR_EMA20_RED and sec_exit < SECTOR_EMA20_RED:
        evidence[CLASS_SECTOR_BREAK] = (
            f"sector went from {sec_entry:+.1f}% to {sec_exit:+.1f}% vs its 20 DMA "
            f"(broke the {SECTOR_EMA20_RED}% line)"
        )

    t1_progress = _f(pp.get("t1_progress_pct"))
    buy_fb_risk = str(row.get("Buy_False_Breakout_Risk") or "").strip().upper()
    if not pp.get("almost_worked") \
            and t1_progress is not None and t1_progress < FALSE_BREAKOUT_MAX_PROGRESS \
            and (pp.get("closed_below_entry_within_3_bars") or buy_fb_risk in ("MEDIUM", "HIGH")):
        why = ("closed back below entry within 3 bars"
               if pp.get("closed_below_entry_within_3_bars")
               else f"entry flagged false-breakout risk {buy_fb_risk}")
        evidence[CLASS_FALSE_BREAKOUT] = (
            f"never reached {FALSE_BREAKOUT_MAX_PROGRESS:.0f}% of the way to T1 and {why}"
        )

    vol_ratio = _f(row.get("Buy_Vol_Ratio"))
    if vol_ratio is not None and vol_ratio < LOW_VOLUME_RATIO:
        evidence[CLASS_LOW_VOLUME_ENTRY] = (
            f"entered on {vol_ratio:.2f}× volume (below {LOW_VOLUME_RATIO:.1f}×)"
        )

    sl_atr = _f(cd.get("sl_distance_atr"))
    # Negative distance = stop trailed above entry; that's not a tight INITIAL stop.
    tight_sl_primary_ok = sl_atr is not None and 0 < sl_atr < TIGHT_SL_ATR_RATIO
    if tight_sl_primary_ok:
        evidence[CLASS_TIGHT_SL] = f"stop was only {sl_atr:.2f}× ATR below entry"
    elif pp.get("almost_worked"):
        # Weaker signal — contributing only until the 5-day re-check confirms.
        evidence[CLASS_TIGHT_SL] = (
            f"reached {t1_progress:.0f}% of the way to T1 before reversing to the stop"
        )

    matched = [c for c in PRECEDENCE if c in evidence]
    primary_eligible = [c for c in matched
                        if c != CLASS_TIGHT_SL or tight_sl_primary_ok]
    primary = primary_eligible[0] if primary_eligible else CLASS_UNKNOWN
    # LOW_VOLUME_ENTRY is a contributing-quality signal: it only takes primary
    # when nothing structural matched before it.
    contributing = [c for c in matched if c != primary]
    return {"primary": primary, "contributing": contributing, "evidence": evidence}


# ── "Why didn't the app see it" ──────────────────────────────────────────────

# class → entry filter whose record (from apply_risk_filters detail=) covers it
_CLASS_FILTER = {
    CLASS_MARKET_REGIME_FLIP: "sector_nifty_regime",
    CLASS_SECTOR_BREAK: "sector_nifty_regime",
    CLASS_LOW_VOLUME_ENTRY: "breakout_volume",
    CLASS_EARNINGS_SURPRISE: "earnings_soon",
}

_ADD_DETAIL = {
    CLASS_MARKET_REGIME_FLIP: "no in-trade regime monitor exits or tightens stops when the Nifty regime flips",
    CLASS_SECTOR_BREAK: "no in-trade sector monitor reacts when the sector breaks its 20 DMA",
    CLASS_LOW_VOLUME_ENTRY: "breakout volume was not evaluated at entry — make the 1.2x volume gate mandatory",
    CLASS_EARNINGS_SURPRISE: "earnings date was unknown at entry — needs a better earnings-calendar source",
    CLASS_GAP_EVENT: "no filter covers overnight gap risk — e.g. cap position size when ATR% is high or events are pending",
    CLASS_FALSE_BREAKOUT: "false-breakout detector rated the entry LOW risk — detector blind spot",
    CLASS_TIGHT_SL: "stop sizing allowed a stop tighter than 1x ATR — enforce a minimum stop distance in the trade plan",
}

# Readable filter labels for classes with no existing covering filter (used
# instead of the bare literal "none" in reports/digests).
_NO_FILTER_LABEL = {
    CLASS_GAP_EVENT: "overnight_gap_risk (no filter yet)",
    CLASS_FALSE_BREAKOUT: "false_breakout_risk_detector",
}
TIGHT_SL_FILTER = "trade_plan_stop_sizing"


def _is_marginal(rec):
    """PASS records that squeaked by their threshold."""
    if rec.get("verdict") != "PASS":
        return False
    margin = _f(rec.get("margin"))
    if margin is None:
        return False
    if rec.get("filter") == "sector_nifty_regime":
        return margin <= SECTOR_MARGIN_PTS
    threshold = _f(rec.get("threshold"))
    if not threshold:
        return False
    return abs(margin) <= MARGINAL_PASS_FRACTION * abs(threshold)


def derive_app_gaps(classification, snapshot_detail):
    """For each classified failure mode, decide TIGHTEN (an existing filter
    passed marginally at entry) vs ADD (no filter covers this mode).

    `snapshot_detail` is entry_snapshot["risk_filters"]["detail"] — the _rec
    records — or None for pre-snapshot trades (partial confidence).
    """
    classes = [classification.get("primary")] + list(classification.get("contributing") or [])
    classes = [c for c in classes if c and c != CLASS_UNKNOWN]
    gaps = []
    for cls in classes:
        filter_name = _CLASS_FILTER.get(cls)
        add_detail = _ADD_DETAIL.get(cls, "no existing filter covers this failure mode")
        no_filter_label = _NO_FILTER_LABEL.get(cls, "none")

        if cls == CLASS_TIGHT_SL:
            # Stop sizing is an existing, tunable parameter (trade plan), not a
            # missing filter — this is always TIGHTEN, snapshot or not.
            detail = add_detail if snapshot_detail is not None else f"(partial — entry snapshot unavailable) {add_detail}"
            gaps.append({"class": cls, "type": "TIGHTEN", "filter": TIGHT_SL_FILTER, "detail": detail})
            continue

        if snapshot_detail is None:
            gaps.append({
                "class": cls, "type": "ADD", "filter": filter_name or no_filter_label,
                "detail": f"(partial — entry snapshot unavailable) {add_detail}",
            })
            continue

        if filter_name is None:
            gaps.append({"class": cls, "type": "ADD",
                         "filter": no_filter_label, "detail": add_detail})
            continue

        rec = next((r for r in snapshot_detail if r.get("filter") == filter_name), None)
        if rec is not None and _is_marginal(rec):
            gaps.append({
                "class": cls, "type": "TIGHTEN", "filter": filter_name,
                "detail": (f"{filter_name} passed marginally at entry "
                           f"(measured {rec.get('measured')}, threshold {rec.get('threshold')}, "
                           f"margin {rec.get('margin')}) — tighten the threshold"),
            })
        else:
            gaps.append({"class": cls, "type": "ADD",
                         "filter": filter_name, "detail": add_detail})
    return gaps


# ── TIGHT_SL deferred re-check ───────────────────────────────────────────────

def evaluate_tight_sl_recovery(bars_after_exit, entry_price, stop_price):
    """Did price recover after stopping us out? STRONG = back above entry,
    WEAK = above stop × 1.02 only."""
    entry_price = _f(entry_price)
    stop_price = _f(stop_price)
    if bars_after_exit is None or not len(bars_after_exit):
        return {"recovered": False, "strength": None, "recovery_close": None}
    max_close = float(bars_after_exit["Close"].max())
    if entry_price is not None and max_close > entry_price:
        return {"recovered": True, "strength": "STRONG", "recovery_close": max_close}
    if stop_price is not None and max_close > stop_price * RECOVERY_WEAK_STOP_MULT:
        return {"recovered": True, "strength": "WEAK", "recovery_close": max_close}
    return {"recovered": False, "strength": None, "recovery_close": None}


# ── Historical regime helpers ────────────────────────────────────────────────
# Same GREEN/AMBER/RED rule as fetch_nifty_levels() (core_data_fetcher.py:460),
# but computable as-of any historical date so entry/exit regimes can be
# reconstructed for trades that predate the entry-snapshot subsystem.

def regime_from_frame(df, as_of):
    closes = df.loc[:as_of, "Close"].dropna()
    if len(closes) < 2:
        return "UNKNOWN"
    price = float(closes.iloc[-1])
    ema20 = float(closes.ewm(span=20).mean().iloc[-1])
    ema50 = float(closes.ewm(span=50).mean().iloc[-1])
    bullish = sum([price > ema20, price > ema50, ema20 > ema50])
    if bullish == 3:
        return "GREEN"
    if bullish == 2:
        return "AMBER"
    return "RED"


def ema20_from_frame(df, as_of):
    """pct_from_ema20 as-of a date — same measure fetch_sector_pulse uses."""
    closes = df.loc[:as_of, "Close"].dropna()
    if len(closes) < 2:
        return None
    price = float(closes.iloc[-1])
    ema20 = float(closes.ewm(span=20).mean().iloc[-1])
    if not ema20:
        return None
    return (price - ema20) / ema20 * 100.0


# ── Weekly digest ────────────────────────────────────────────────────────────

def build_weekly_digest_text(aggregate, week_losses):
    """Telegram-HTML weekly digest. None when there is nothing to report."""
    total = int(aggregate.get("total") or 0)
    if total == 0 and not week_losses:
        return None

    lines = ["📋 <b>Weekly SL Post-Mortem Digest</b>", ""]

    if week_losses:
        lines.append("<b>Stopped out this week:</b>")
        for loss in week_losses:
            lines.append(f"  • {loss.get('symbol')} — {loss.get('primary')} ({loss.get('exit_date')})")
        lines.append("")

    by_class = aggregate.get("by_class") or {}
    if by_class:
        lines.append(f"<b>All-time failure classes</b> ({total} analyzed):")
        top = sorted(by_class.items(), key=lambda kv: kv[1], reverse=True)[:5]
        for cls, n in top:
            lines.append(f"  • {cls}: {n}")
        lines.append("")

    gaps = aggregate.get("gaps") or []
    if gaps:
        g = max(gaps, key=lambda x: x.get("count", 0))
        lines.append(f"<b>Top fix:</b> {g.get('type')} {g.get('filter')} — seen in {g.get('count')} losses")

    pending = int(aggregate.get("pending_rechecks") or 0)
    if pending:
        lines.append(f"⏳ {pending} tight-SL re-check(s) pending")

    partial = int(aggregate.get("partial_count") or 0)
    if partial:
        lines.append(f"ℹ️ {partial} of {total} analyzed without an entry snapshot (partial)")

    return "\n".join(lines).strip()


# ═════════════════════════════════════════════════════════════════════════════
# Impure layer — fetching, orchestration, sidecar IO, CSV sync, CLI
# ═════════════════════════════════════════════════════════════════════════════

SCHEMA_VERSION = 1
RECHECK_CALENDAR_DAYS = 7      # ≈ 5 trading days after the stop-out
RECHECK_MIN_BARS = 5
RECHECK_GIVE_UP_DAYS = 21      # no bars after this long → close the re-check
BACKFILL_SLEEP_S = 2.0


def _pm_dir(root=None):
    return os.path.join(root or _ROOT, "data", "post_mortems")


def _positions_path(root=None):
    return os.path.join(root or _ROOT, "data", "positions.csv")


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _resolve_ticker(symbol):
    sym = str(symbol).strip().upper()
    try:
        try:
            from core_data_fetcher import NSE_TICKERS
        except ImportError:
            from core.core_data_fetcher import NSE_TICKERS
        return NSE_TICKERS.get(sym, f"{sym}.NS")
    except Exception:
        return f"{sym}.NS"


def fetch_trade_bars(symbol, start_date, end_date):
    """Daily OHLCV for an arbitrary historical window (start/end, not period=,
    because SL losses can be older than any rolling lookback)."""
    import yfinance as yf
    end_pad = (pd.Timestamp(end_date) + timedelta(days=3)).strftime("%Y-%m-%d")
    df = yf.Ticker(_resolve_ticker(symbol)).history(start=start_date, end=end_pad)
    return df.dropna(subset=["Close"]) if df is not None and not df.empty else pd.DataFrame()


def _fetch_index_frame(ticker, start_date, end_date):
    """Index history with ~1y warm-up before start so EMAs are meaningful."""
    import yfinance as yf
    warm_start = (pd.Timestamp(start_date) - timedelta(days=365)).strftime("%Y-%m-%d")
    end_pad = (pd.Timestamp(end_date) + timedelta(days=3)).strftime("%Y-%m-%d")
    df = yf.Ticker(ticker).history(start=warm_start, end=end_pad)
    return df.dropna(subset=["Close"]) if df is not None and not df.empty else pd.DataFrame()


def load_entry_snapshot(symbol, entry_date, root=None):
    path = os.path.join(root or _ROOT, "data", "entry_snapshots",
                        f"{str(symbol).upper()}_{entry_date}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _earnings_in_window(symbol, entry_date, exit_date, root=None):
    """(in_window, date) — best effort; (None, None) when no source resolves.
    Note fetch_earnings_date() in core_risk_filters only returns *upcoming*
    dates, so historical windows need the cache / yfinance history instead."""
    sym = str(symbol).strip().upper()
    dates = []
    try:
        cache_path = os.path.join(root or _ROOT, "data", "earnings_cache.json")
        with open(cache_path, "r", encoding="utf-8") as f:
            entry = json.load(f).get(sym) or {}
        for key in ("upcoming", "past"):
            d = (entry.get(key) or {}).get("date")
            if d:
                dates.append(d)
    except (OSError, ValueError):
        pass
    if not dates:
        try:
            import yfinance as yf
            edf = yf.Ticker(_resolve_ticker(sym)).get_earnings_dates(limit=12)
            if edf is not None and not edf.empty:
                dates = [ts.strftime("%Y-%m-%d") for ts in edf.index]
        except Exception:
            pass
    if not dates:
        return None, None
    hits = [d for d in dates if entry_date <= d <= exit_date]
    if hits:
        return True, hits[0]
    return False, None


def build_exit_context(symbol, entry_date, exit_date, entry_snapshot, row, root=None):
    """Assemble the condition_diff block. Every sub-piece is best-effort;
    what can't be reconstructed becomes None plus a confidence note."""
    notes = []
    cd = {
        "nifty_regime_entry": None, "nifty_regime_exit": None,
        "nifty_regime_source": "reconstructed", "nifty_change_pct": None,
        "sector": None, "sector_pct_ema20_entry": None, "sector_pct_ema20_exit": None,
        "earnings_in_window": None, "earnings_date": None,
        "vol_ratio_entry": _f(row.get("Buy_Vol_Ratio")),
        "atr_pct_entry": _f(row.get("Buy_ATR_Pct")),
        "sl_distance_pct": None, "sl_distance_atr": None,
    }

    ep = _f(row.get("Entry_Price"))
    sl = _f(row.get("Current_SL"))
    if ep and sl and ep > 0:
        cd["sl_distance_pct"] = round((ep - sl) / ep * 100.0, 2)
        if cd["atr_pct_entry"]:
            cd["sl_distance_atr"] = round(cd["sl_distance_pct"] / cd["atr_pct_entry"], 2)

    # Nifty regime at entry (snapshot if available) and exit (always reconstructed)
    snap_regime = None
    if entry_snapshot:
        snap_regime = ((entry_snapshot.get("regime") or {}).get("nifty") or {}).get("regime")
    try:
        ndf = _fetch_index_frame("^NSEI", entry_date, exit_date)
        if len(ndf):
            cd["nifty_regime_exit"] = regime_from_frame(ndf, exit_date)
            if snap_regime:
                cd["nifty_regime_entry"] = snap_regime
                cd["nifty_regime_source"] = "snapshot"
            else:
                cd["nifty_regime_entry"] = regime_from_frame(ndf, entry_date)
            window = ndf.loc[entry_date:exit_date, "Close"]
            if len(window) >= 2:
                cd["nifty_change_pct"] = round(
                    (float(window.iloc[-1]) - float(window.iloc[0]))
                    / float(window.iloc[0]) * 100.0, 2)
        else:
            notes.append("nifty history unavailable")
    except Exception as e:
        logger.warning("[post_mortem] nifty context failed for %s: %s", symbol, e)
        notes.append("nifty history unavailable")

    # Sector pct_from_ema20 at entry/exit
    try:
        try:
            from core_sectors import get_sector, SECTOR_INDEX
        except ImportError:
            from core.core_sectors import get_sector, SECTOR_INDEX
        sector = get_sector(symbol)
        cd["sector"] = sector
        ticker = SECTOR_INDEX.get(sector)
        snap_sector_pct = None
        if entry_snapshot:
            snap_sector_pct = ((entry_snapshot.get("regime") or {}).get("sector") or {}).get("pct_from_ema20")
        if ticker:
            sdf = _fetch_index_frame(ticker, entry_date, exit_date)
            if len(sdf):
                cd["sector_pct_ema20_exit"] = ema20_from_frame(sdf, exit_date)
                cd["sector_pct_ema20_entry"] = (
                    snap_sector_pct if snap_sector_pct is not None
                    else ema20_from_frame(sdf, entry_date))
            else:
                notes.append("sector index history unavailable")
        else:
            cd["sector_pct_ema20_entry"] = snap_sector_pct
            notes.append(f"no index for sector {sector} — sector break not evaluable")
    except Exception as e:
        logger.warning("[post_mortem] sector context failed for %s: %s", symbol, e)
        notes.append("sector context unavailable")

    # Earnings inside the hold window
    try:
        in_win, e_date = _earnings_in_window(symbol, entry_date, exit_date, root=root)
        cd["earnings_in_window"] = in_win
        cd["earnings_date"] = e_date
        if in_win is None:
            notes.append("earnings data unavailable")
    except Exception as e:
        logger.warning("[post_mortem] earnings context failed for %s: %s", symbol, e)
        notes.append("earnings data unavailable")

    return cd, notes


# ── LLM narrative (optional; never load-bearing) ─────────────────────────────

_NARRATIVE_SYSTEM = (
    "You are a swing-trading post-mortem analyst. You are given a fully "
    "computed, deterministic classification of why a stop-loss trade failed. "
    "Write a short plain-English post-mortem (3-5 sentences) for a beginner "
    "trader: what happened on the way from entry to stop, why the failure "
    "class fits, and what the app should change (tighten vs add a filter). "
    "Use ONLY the numbers provided — never invent any figure."
)


def generate_narrative(result):
    """LLM rephrasing of the classified result. Any failure degrades to
    {text: None, error: ...} — classification is already complete."""
    out = {"text": None, "provider": None, "model": None, "error": None}
    try:
        try:
            from debate_orchestrator import load_debate_config, run_llm_call
        except ImportError:
            from core.debate_orchestrator import load_debate_config, run_llm_call
        cfg = (load_debate_config() or {}).get("judge_agent") or {}
        provider = cfg.get("provider", "gemini")
        model = cfg.get("model", "gemini-1.5-pro")
        out["provider"], out["model"] = provider, model
        payload = {
            "symbol": result.get("symbol"),
            "classification": result.get("classification"),
            "price_path": result.get("price_path"),
            "condition_diff": result.get("condition_diff"),
            "app_gaps": result.get("app_gaps"),
            "confidence": result.get("confidence"),
        }
        text = run_llm_call(provider, model, _NARRATIVE_SYSTEM,
                            json.dumps(payload, default=str),
                            float(cfg.get("temperature", 0.3)))
        out["text"] = (text or "").strip() or None
    except Exception as e:
        out["error"] = str(e)
        logger.warning("[post_mortem] narrative failed: %s", e)
    return out


# ── Sidecar IO ───────────────────────────────────────────────────────────────

def save_post_mortem(result, root=None):
    pm_dir = _pm_dir(root)
    os.makedirs(pm_dir, exist_ok=True)
    path = os.path.join(pm_dir, f"{result['symbol']}_{result['entry_date']}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    os.replace(tmp, path)
    return path


def load_post_mortem(symbol, entry_date, root=None):
    path = os.path.join(_pm_dir(root), f"{str(symbol).upper()}_{entry_date}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def list_post_mortems(root=None):
    pm_dir = _pm_dir(root)
    results = []
    if not os.path.isdir(pm_dir):
        return results
    for name in sorted(os.listdir(pm_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(pm_dir, name), "r", encoding="utf-8") as f:
                results.append(json.load(f))
        except (OSError, ValueError):
            logger.warning("[post_mortem] unreadable sidecar %s", name)
    return results


def aggregate_post_mortems(root=None):
    """Failure-class counts + per-filter TIGHTEN/ADD miss rates for the
    dashboard and the weekly digest."""
    by_class, gap_counts = {}, {}
    total = partial = pending = upgrades = 0
    for pm in list_post_mortems(root):
        total += 1
        if pm.get("confidence") == "partial":
            partial += 1
        primary = (pm.get("classification") or {}).get("primary") or CLASS_UNKNOWN
        by_class[primary] = by_class.get(primary, 0) + 1
        for g in pm.get("app_gaps") or []:
            key = (g.get("filter"), g.get("type"))
            gap_counts[key] = gap_counts.get(key, 0) + 1
        recheck = pm.get("tight_sl_recheck") or {}
        if not recheck.get("done"):
            pending += 1
        elif recheck.get("recovered"):
            upgrades += 1
    gaps = [{"filter": f, "type": t, "count": n}
            for (f, t), n in sorted(gap_counts.items(), key=lambda kv: -kv[1])]
    return {"by_class": by_class, "total": total, "partial_count": partial,
            "gaps": gaps, "pending_rechecks": pending, "tight_sl_upgrades": upgrades}


def collect_week_losses(root=None, days=7):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for pm in list_post_mortems(root):
        if (pm.get("exit_date") or "") >= cutoff:
            out.append({"symbol": pm.get("symbol"),
                        "primary": (pm.get("classification") or {}).get("primary"),
                        "exit_date": pm.get("exit_date")})
    return out


# ── Orchestrator ─────────────────────────────────────────────────────────────

def run_post_mortem(symbol, entry_date, row, root=None, with_llm=True):
    """Full pipeline for one SL loss: bars → path → context diff → classify →
    app gaps → optional narrative → sidecar. Never writes positions.csv."""
    symbol = str(symbol).strip().upper()
    exit_date = str(row.get("SL_Hit_Date") or "").strip() or _today_str()
    notes = []

    snapshot = load_entry_snapshot(symbol, entry_date, root=root)
    if snapshot is None:
        notes.append("no entry snapshot — pre-snapshot trade, partial analysis")

    bars = fetch_trade_bars(symbol, entry_date, exit_date)
    window = bars.loc[entry_date:exit_date] if len(bars) else bars
    if not len(window):
        notes.append("no price bars for hold window")
        price_path = {
            "entry_price": _f(row.get("Entry_Price")), "stop_price": _f(row.get("Current_SL")),
            "exit_price": _f(row.get("Closing_Price")), "t1": _f(row.get("Target_1")),
            "days_held": None, "bars": 0, "descent_type": "GRIND",
            "worst_gap_down_pct": None, "gap_bar_date": None,
            "hh_since_entry": _f(row.get("Highest_High_Since_Entry")),
            "t1_progress_pct": None, "almost_worked": False,
            "exit_open_below_stop": False, "closed_below_entry_within_3_bars": False,
        }
    else:
        price_path = analyze_price_path(
            window, row.get("Entry_Price"), row.get("Current_SL"),
            row.get("Target_1"), entry_date, exit_date)

    condition_diff, ctx_notes = build_exit_context(
        symbol, entry_date, exit_date, snapshot, row, root=root)
    notes.extend(ctx_notes)

    classification = classify(price_path, condition_diff, row)
    snapshot_detail = None
    if snapshot:
        snapshot_detail = (snapshot.get("risk_filters") or {}).get("detail")
    app_gaps = derive_app_gaps(classification, snapshot_detail)

    due = (pd.Timestamp(exit_date) + timedelta(days=RECHECK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    result = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol, "entry_date": entry_date, "exit_date": exit_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "confidence": "full" if snapshot_detail else "partial",
        "confidence_notes": notes,
        "price_path": price_path,
        "condition_diff": condition_diff,
        "classification": classification,
        "app_gaps": app_gaps,
        "llm_narrative": {"text": None, "provider": None, "model": None, "error": None},
        "tight_sl_recheck": {"due_date": due, "done": False, "checked_on": None,
                             "recovered": None, "recovery_close": None},
    }
    if with_llm:
        result["llm_narrative"] = generate_narrative(result)

    save_post_mortem(result, root=root)
    return result


# ── CSV sync + TIGHT_SL re-checks (single-writer contexts only) ──────────────

CSV_COLS = ["Failure_Class", "Failure_Contributing", "PM_Confidence", "PM_Generated_At"]


def _read_positions(root=None):
    """All-string read so writing back can never trip the pandas-3.0
    str-column dtype trap and non-touched values round-trip verbatim."""
    path = _positions_path(root)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in CSV_COLS:
        if col not in df.columns:
            df[col] = ""
    return df, path


def _write_positions(df, path):
    tmp = f"{path}.tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def _apply_result_to_row(df, idx, pm):
    cls = pm.get("classification") or {}
    df.at[idx, "Failure_Class"] = str(cls.get("primary") or "")
    df.at[idx, "Failure_Contributing"] = ",".join(cls.get("contributing") or [])
    df.at[idx, "PM_Confidence"] = str(pm.get("confidence") or "")
    df.at[idx, "PM_Generated_At"] = str(pm.get("generated_at") or "")


def _process_recheck(pm, notify=None):
    """Evaluate one due TIGHT_SL re-check in place. Returns True if updated."""
    recheck = pm.get("tight_sl_recheck") or {}
    if recheck.get("done") or (recheck.get("due_date") or "9999") > _today_str():
        return False
    exit_date = pm.get("exit_date") or _today_str()
    start = (pd.Timestamp(exit_date) + timedelta(days=1)).strftime("%Y-%m-%d")
    bars = fetch_trade_bars(pm["symbol"], start, _today_str())
    days_since = (pd.Timestamp(_today_str()) - pd.Timestamp(exit_date)).days
    if len(bars) < RECHECK_MIN_BARS and days_since < RECHECK_GIVE_UP_DAYS:
        return False  # not enough bars yet — try again on a later sweep

    pp = pm.get("price_path") or {}
    verdict = evaluate_tight_sl_recovery(bars, pp.get("entry_price"), pp.get("stop_price"))
    recheck.update({"done": True, "checked_on": _today_str(),
                    "recovered": verdict["recovered"],
                    "recovery_close": verdict["recovery_close"]})
    pm["tight_sl_recheck"] = recheck

    if verdict["recovered"]:
        cls = pm.get("classification") or {}
        strength = verdict.get("strength")
        cls.setdefault("evidence", {})[CLASS_TIGHT_SL] = (
            f"price recovered to {verdict['recovery_close']:.2f} within days of the "
            f"stop-out ({strength}) — the stop was too tight")
        primary = cls.get("primary")
        upgradable = primary == CLASS_UNKNOWN or (
            primary == CLASS_FALSE_BREAKOUT and pp.get("almost_worked"))
        if upgradable:
            if primary and primary != CLASS_UNKNOWN and primary not in (cls.get("contributing") or []):
                cls.setdefault("contributing", []).append(primary)
            cls["primary"] = CLASS_TIGHT_SL
            cls["contributing"] = [c for c in cls.get("contributing", []) if c != CLASS_TIGHT_SL]
        elif CLASS_TIGHT_SL not in (cls.get("contributing") or []) and primary != CLASS_TIGHT_SL:
            cls.setdefault("contributing", []).append(CLASS_TIGHT_SL)
        pm["classification"] = cls
        if notify:
            try:
                notify(f"♻️ <b>TIGHT_SL confirmed — {pm['symbol']}</b>\n"
                       f"Price recovered to ₹{verdict['recovery_close']:.2f} "
                       f"({strength}) within days of the stop-out.")
            except Exception as e:
                logger.warning("[post_mortem] recheck notify failed: %s", e)
    return True


def sync_csv_and_rechecks(root=None, notify=None, with_llm=True):
    """Daily sweep body. MUST only run when the poller is idle (single writer):
    1. run post-mortems for SL_LOSS rows without a sidecar,
    2. process due TIGHT_SL re-checks,
    3. copy sidecar classifications into positions.csv columns.
    """
    stats = {"created": 0, "rechecked": 0, "synced": 0, "errors": 0}
    df, path = _read_positions(root)
    dirty = False

    for idx, r in df.iterrows():
        if str(r.get("Outcome") or "").strip().upper() != "SL_LOSS":
            continue
        sym = str(r.get("Symbol") or "").strip().upper()
        entry_date = str(r.get("Entry_Date") or "").strip()
        if not sym or not entry_date:
            continue
        try:
            pm = load_post_mortem(sym, entry_date, root=root)
            if pm is None:
                pm = run_post_mortem(sym, entry_date, r.to_dict(), root=root, with_llm=with_llm)
                stats["created"] += 1
            if _process_recheck(pm, notify=notify):
                save_post_mortem(pm, root=root)
                stats["rechecked"] += 1
            if str(df.at[idx, "Failure_Class"]) != str((pm.get("classification") or {}).get("primary") or "") \
                    or str(df.at[idx, "PM_Generated_At"]) != str(pm.get("generated_at") or ""):
                _apply_result_to_row(df, idx, pm)
                stats["synced"] += 1
                dirty = True
        except Exception as e:
            stats["errors"] += 1
            logger.error("[post_mortem] sweep failed for %s/%s: %s", sym, entry_date, e)

    if dirty:
        _write_positions(df, path)
    return stats


def run_backfill(root=None, symbols=None, with_llm=False, sleep_s=BACKFILL_SLEEP_S, force=False):
    """One-off pass over historical SL_LOSS rows. Run with the server stopped."""
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    df, _ = _read_positions(root)
    wanted = {s.strip().upper() for s in symbols} if symbols else None
    for _, r in df.iterrows():
        if str(r.get("Outcome") or "").strip().upper() != "SL_LOSS":
            continue
        sym = str(r.get("Symbol") or "").strip().upper()
        entry_date = str(r.get("Entry_Date") or "").strip()
        if not sym or not entry_date or (wanted and sym not in wanted):
            continue
        if not force and load_post_mortem(sym, entry_date, root=root) is not None:
            stats["skipped"] += 1
            continue
        try:
            pm = run_post_mortem(sym, entry_date, r.to_dict(), root=root, with_llm=with_llm)
            stats["processed"] += 1
            print(f"  {sym} {entry_date}: {pm['classification']['primary']} "
                  f"({pm['confidence']})")
        except Exception as e:
            stats["errors"] += 1
            print(f"  {sym} {entry_date}: ERROR {e}")
        time.sleep(sleep_s)
    # Push results into the CSV columns (safe: server is stopped for backfill)
    sync = sync_csv_and_rechecks(root=root, with_llm=with_llm)
    stats["synced"] = sync["synced"]
    return stats


# ── Telegram (standalone — no Flask import needed for the CLI/CI path) ───────

def _send_telegram(msg: str) -> bool:
    """Send a Telegram message using TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env
    vars. Mirrors server.py:_tg_send (same HTML parse_mode). Silently skips
    if credentials aren't configured. Returns whether a send was attempted."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        return False
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning("[post_mortem] telegram send failed: %s", e)
    return True


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main():
    ap = argparse.ArgumentParser(description="SL post-mortem engine (issue #3)")
    ap.add_argument("--backfill", action="store_true", help="analyze all SL_LOSS rows lacking a sidecar (run with server stopped)")
    ap.add_argument("--symbol", help="analyze one symbol (with --entry-date)")
    ap.add_argument("--entry-date", help="entry date YYYY-MM-DD for --symbol")
    ap.add_argument("--sweep", action="store_true", help="run the daily sync + re-check pass once")
    ap.add_argument("--digest", action="store_true", help="print the weekly digest text")
    ap.add_argument("--send", action="store_true", help="with --digest, send to Telegram instead of printing")
    ap.add_argument("--with-llm", action="store_true", help="also generate the LLM narrative")
    ap.add_argument("--force", action="store_true", help="re-analyze even if a sidecar exists")
    ap.add_argument("--sleep", type=float, default=BACKFILL_SLEEP_S, help="seconds between backfill symbols")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252; digest/summary text has emoji
    except Exception:
        pass

    if args.backfill:
        stats = run_backfill(with_llm=args.with_llm, sleep_s=args.sleep, force=args.force)
        print(f"backfill done: {stats}")
    elif args.symbol and args.entry_date:
        df, _ = _read_positions()
        sym = args.symbol.strip().upper()
        rows = df[(df["Symbol"].str.upper() == sym) & (df["Entry_Date"] == args.entry_date)]
        if rows.empty:
            print(f"no positions.csv row for {sym} @ {args.entry_date}")
            return
        pm = run_post_mortem(sym, args.entry_date, rows.iloc[0].to_dict(), with_llm=args.with_llm)
        print(json.dumps(pm, indent=2, default=str))
    elif args.sweep:
        print(f"sweep done: {sync_csv_and_rechecks(with_llm=args.with_llm)}")
    elif args.digest:
        text = build_weekly_digest_text(aggregate_post_mortems(), collect_week_losses())
        if args.send:
            if text and _send_telegram(text):
                print("digest sent")
            elif not text:
                print("nothing to report — digest not sent")
            else:
                print("digest not sent — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured")
        else:
            print(text or "(nothing to report)")
    else:
        ap.print_help()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _main()

