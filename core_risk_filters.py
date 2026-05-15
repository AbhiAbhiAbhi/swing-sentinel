"""
Pre-trade risk filters — applied AFTER Chartink scan returns candidates.

Each filter takes the live tech dict (from fetch_stock_technicals) and the
raw history DataFrame, returns (passed: bool, reason: str).

If ANY filter fails, the stock is excluded from /api/scan results and shown
in `filtered_out` with the reason — fully transparent.

Conservative thresholds (block obvious risks, don't over-prune):
  - Earnings within 3 trading days        → skip
  - IPO age less than 180 days            → skip
  - ATR / price > 5.0%                    → skip (too volatile)
  - Any single-day drop ≤ -8% in 60 days  → skip (recent crash)
  - Sector index below its EMA20          → skip (weak sector)
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Thresholds (Conservative) ───────────────────────────────────────────────

EARNINGS_WINDOW_DAYS  = 3
IPO_MIN_AGE_DAYS      = 180
MAX_ATR_PCT           = 0.05   # 5%
WORST_60D_DROP_PCT    = -0.08  # -8%


# ── Individual filters ──────────────────────────────────────────────────────

def filter_volatility(tech: dict) -> Tuple[bool, str]:
    """Reject if ATR is more than MAX_ATR_PCT of the current price."""
    price = tech.get("price", 0)
    atr   = tech.get("atr", 0)
    if not price or not atr:
        return True, ""
    ratio = atr / price
    if ratio > MAX_ATR_PCT:
        return False, f"high volatility (ATR {ratio*100:.1f}% of price)"
    return True, ""


def filter_recent_crash(tech: dict) -> Tuple[bool, str]:
    """Reject if any single-day return in the last 60 daily bars was <= -8%."""
    worst = tech.get("worst_60d_pct", 0)
    if worst and worst <= WORST_60D_DROP_PCT:
        return False, f"recent crash ({worst*100:.1f}% drop in last 60d)"
    return True, ""


def filter_ipo_age(tech: dict) -> Tuple[bool, str]:
    """Reject if the stock has fewer than IPO_MIN_AGE_DAYS of trading history."""
    bars = tech.get("bars_count", 999)
    if bars < IPO_MIN_AGE_DAYS:
        first_bar = tech.get("first_bar", "")
        return False, f"recent IPO ({bars} bars{', since '+first_bar if first_bar else ''})"
    return True, ""


def filter_earnings_soon(symbol: str) -> Tuple[bool, str]:
    """Reject if earnings announcement is scheduled in the next 3 trading days."""
    try:
        import yfinance as yf
        cal = yf.Ticker(f"{symbol}.NS").calendar
        if not cal:
            return True, ""

        earnings_date = None
        # yfinance returns either a dict or a DataFrame depending on version
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, list) and ed:
                earnings_date = ed[0]
            elif ed:
                earnings_date = ed
        else:
            try:
                earnings_date = cal.loc["Earnings Date"].iloc[0]
            except Exception:
                pass

        if not earnings_date:
            return True, ""

        # Normalize to date
        if hasattr(earnings_date, "date"):
            edate = earnings_date.date() if hasattr(earnings_date, "date") else earnings_date
        else:
            edate = earnings_date

        today      = datetime.now().date()
        days_until = (edate - today).days if hasattr(edate, "__sub__") else 999
        if 0 <= days_until <= EARNINGS_WINDOW_DAYS:
            return False, f"earnings in {days_until}d ({edate})"
    except Exception as exc:
        logger.debug("[filter_earnings_soon] %s: %s", symbol, exc)
    return True, ""


def filter_weak_sector(symbol: str, sector_pulse: Optional[dict] = None) -> Tuple[bool, str]:
    """Reject if the stock's sector index is currently below its EMA20."""
    try:
        from core_sectors import get_sector, is_sector_in_uptrend
        sector = get_sector(symbol)
        if sector == "OTHERS":
            return True, ""   # don't penalize unmapped stocks
        if not is_sector_in_uptrend(symbol, pulse=sector_pulse):
            return False, f"weak sector ({sector})"
    except Exception as exc:
        logger.debug("[filter_weak_sector] %s: %s", symbol, exc)
    return True, ""


# ── Master filter ───────────────────────────────────────────────────────────

def apply_risk_filters(symbol: str, tech: dict,
                       sector_pulse: Optional[dict] = None) -> Tuple[bool, list]:
    """
    Run all 5 filters. Returns (passed_all, reasons_failed).
    `tech` is the dict from fetch_stock_technicals (must include
    'worst_60d_pct', 'bars_count' fields added in core_data_fetcher.py).
    `sector_pulse` is the cached sector dict (pass-through to avoid
    re-fetching per stock).
    """
    reasons = []
    for fn, args in [
        (filter_volatility,    (tech,)),
        (filter_recent_crash,  (tech,)),
        (filter_ipo_age,       (tech,)),
        (filter_earnings_soon, (symbol,)),
        (filter_weak_sector,   (symbol, sector_pulse)),
    ]:
        passed, reason = fn(*args)
        if not passed and reason:
            reasons.append(reason)
    return (len(reasons) == 0), reasons
