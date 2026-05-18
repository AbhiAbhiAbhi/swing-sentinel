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

def filter_volatility(tech: dict, max_atr_pct: float = MAX_ATR_PCT) -> Tuple[bool, str]:
    """Reject if ATR is more than max_atr_pct of the current price."""
    price = tech.get("price", 0)
    atr   = tech.get("atr", 0)
    if not price or not atr:
        return True, ""
    ratio = atr / price
    if ratio > max_atr_pct:
        return False, f"high volatility (ATR {ratio*100:.1f}% of price)"
    return True, ""


def filter_recent_crash(tech: dict, worst_pct: float = WORST_60D_DROP_PCT) -> Tuple[bool, str]:
    """Reject if any single-day return in the last 60 daily bars was <= worst_pct."""
    worst = tech.get("worst_60d_pct", 0)
    if worst and worst <= worst_pct:
        return False, f"recent crash ({worst*100:.1f}% drop in last 60d)"
    return True, ""


def filter_ipo_age(tech: dict, min_days: int = IPO_MIN_AGE_DAYS) -> Tuple[bool, str]:
    """Reject if the stock has fewer than min_days of trading history."""
    bars = tech.get("bars_count", 999)
    if bars < min_days:
        first_bar = tech.get("first_bar", "")
        return False, f"recent IPO ({bars} bars{', since '+first_bar if first_bar else ''})"
    return True, ""


def filter_earnings_soon(symbol: str, window_days: int = EARNINGS_WINDOW_DAYS) -> Tuple[bool, str]:
    """Reject if earnings announcement is scheduled within window_days trading days."""
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
        if 0 <= days_until <= window_days:
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
                       sector_pulse: Optional[dict] = None,
                       thresholds: Optional[dict] = None) -> Tuple[bool, list]:
    """
    Run all 5 filters. Returns (passed_all, reasons_failed).

    `thresholds` is an optional dict from the UI with any of:
      max_atr_pct (float %, e.g. 5.0), max_1d_drop_pct (float %, e.g. -8.0),
      min_ipo_days (int), earnings_window_days (int), block_weak_sectors (bool).
    Missing keys fall back to the module-level constants.
    """
    t = thresholds or {}
    max_atr  = t.get("max_atr_pct",          MAX_ATR_PCT * 100) / 100
    worst_60 = t.get("max_1d_drop_pct",       WORST_60D_DROP_PCT * 100) / 100
    min_ipo  = int(t.get("min_ipo_days",       IPO_MIN_AGE_DAYS))
    earn_win = int(t.get("earnings_window_days", EARNINGS_WINDOW_DAYS))
    block_sec = bool(t.get("block_weak_sectors", True))

    checks = [
        (filter_volatility,    (tech, max_atr)),
        (filter_recent_crash,  (tech, worst_60)),
        (filter_ipo_age,       (tech, min_ipo)),
        (filter_earnings_soon, (symbol, earn_win)),
    ]
    if block_sec:
        checks.append((filter_weak_sector, (symbol, sector_pulse)))

    reasons = []
    for fn, args in checks:
        passed, reason = fn(*args)
        if not passed and reason:
            reasons.append(reason)
    return (len(reasons) == 0), reasons
