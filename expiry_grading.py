"""
Setup Grading + F&O Expiry Context
----------------------------------
Pure, deterministic logic. No network, no side effects — mirrors the style of
core_trade_plan.py and scoring.py.

Two responsibilities, deliberately separated because they answer different
questions at different moments:

  grade_setup(tech, plan)        -> A/B/C quality grade. A property of the SETUP,
                                    evaluated at ANALYSIS time (Trading tab).

  expiry_context(today, ...)     -> F&O expiry-window flag + position-size
                                    multiplier. A property of the FILL DATE,
                                    so it is evaluated live against the current
                                    date for OPEN positions (Portfolio tab),
                                    NOT frozen at analysis time.

Both consume data your pipeline already produces:
  - `tech`  from fetch_stock_technicals()   (core_data_fetcher.py)
  - `plan`  from calculate_trade_plan()      (core_trade_plan.py)
  - is_fno  from "F&O" in get_index_membership(symbol)  (server.py)
"""
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Set


# ── Tunables (one place to change them; calibrate from backtest.py) ──────────
class GradingConfig:
    # Grade thresholds — score is out of 6.0
    GRADE_A_CUTOFF = 5.0
    GRADE_B_CUTOFF = 3.5

    # Factor thresholds (mirror core_risk_filters.py conventions where possible)
    VOL_STRONG     = 1.5     # volume_ratio for full marks
    VOL_OK         = 1.0
    RR_STRONG      = 2.5     # rr_ratio for full marks
    RR_OK          = 1.5
    ROOM_STRONG    = 5.0     # % upside to T2 for full marks
    ROOM_OK        = 2.5
    OVEREXT_RUNUP  = 25.0    # return_20d above this = chasing (matches filter_overextended_1m)
    ADX_TREND      = 20.0    # matches Chartink adx_min default

    # F&O expiry window
    EXPIRY_WEEKDAY     = 1    # Mon=0, Tue=1. NSE monthly = last Tuesday (as of 2026)
    STANDARD_BACK_DAYS = 1    # window = T-1 .. T+1
    CONSERVATIVE_BACK  = 3    # window = T-3 .. T+1
    FORWARD_DAYS       = 1
    WINDOW_MULTIPLIER  = 0.5  # A-grade size inside window


# ── Setup grading (analysis-time) ────────────────────────────────────────────
def grade_setup(tech: Dict, plan: Dict, cfg: GradingConfig = GradingConfig) -> Dict:
    """
    Score a swing-long setup A/B/C from the tech dict + trade plan you already
    build. Six factors, each 0 / 0.5 / 1.0, summed to a 6-point score. Pure.

    Returns: {grade, score, max, breakdown:{trend,structure,volume,rr,room,landmines}}
    """
    price = float(tech.get("price", 0) or 0)
    ema20 = float(tech.get("ema20", 0) or 0)
    ema50 = float(tech.get("ema50", 0) or 0)
    s: Dict[str, float] = {}

    # 1. Trend alignment: price > EMA20 > EMA50 AND weekly bullish
    bits = sum([
        bool(price and ema20 and price > ema20),
        bool(ema20 and ema50 and ema20 > ema50),
        tech.get("weekly_trend") == "BULLISH",
    ])
    s["trend"] = 1.0 if bits == 3 else 0.5 if bits == 2 else 0.0

    # 2. Clean structure: real setup type + stable base + low false-breakout risk
    setup = plan.get("setup_type", "")
    structured = setup in ("BREAKOUT", "PULLBACK", "SUPPORT_BOUNCE")
    clean = (tech.get("base_status") in ("STABLE_BASE", "CONSOLIDATING")
             and tech.get("false_breakout_risk", "LOW") == "LOW")
    s["structure"] = 1.0 if (structured and clean) else 0.5 if (structured or clean) else 0.0

    # 3. Volume confirmation
    vr = float(tech.get("volume_ratio", 1.0) or 1.0)
    s["volume"] = 1.0 if vr >= cfg.VOL_STRONG else 0.5 if vr >= cfg.VOL_OK else 0.0

    # 4. Risk:reward (from trade plan)
    rr = float(plan.get("rr_ratio", 0) or 0)
    s["rr"] = 1.0 if rr >= cfg.RR_STRONG else 0.5 if rr >= cfg.RR_OK else 0.0

    # 5. Room to target (T2 upside %)
    t2 = float(plan.get("target_2", 0) or 0)
    room = ((t2 - price) / price * 100) if price else 0.0
    s["room"] = 1.0 if room >= cfg.ROOM_STRONG else 0.5 if room >= cfg.ROOM_OK else 0.0

    # 6. No landmines: not overextended (chasing) + trend has strength (ADX)
    landmine = 1.0
    if float(tech.get("return_20d", 0) or 0) > cfg.OVEREXT_RUNUP:
        landmine -= 0.5
    if float(tech.get("adx", 0) or 0) < cfg.ADX_TREND:
        landmine -= 0.5
    s["landmines"] = max(0.0, landmine)

    score = round(sum(s.values()), 2)
    grade = ("A" if score >= cfg.GRADE_A_CUTOFF
             else "B" if score >= cfg.GRADE_B_CUTOFF
             else "C")
    return {"grade": grade, "score": score, "max": 6, "breakdown": s}


# ── F&O expiry context (fill-time / live) ────────────────────────────────────
def _is_trading_day(d: date, holidays: Set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def _step(d: date, direction: int, holidays: Set[str]) -> date:
    nxt = d
    while True:
        nxt = nxt + timedelta(days=direction)
        if _is_trading_day(nxt, holidays):
            return nxt


def monthly_expiry(year: int, month: int, holidays: Set[str],
                   cfg: GradingConfig = GradingConfig) -> date:
    """Last EXPIRY_WEEKDAY of the month, rolled back to prior trading day if holiday."""
    nxt_month = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt_month - timedelta(days=1)
    while d.weekday() != cfg.EXPIRY_WEEKDAY:
        d -= timedelta(days=1)
    while not _is_trading_day(d, holidays):
        d = _step(d, -1, holidays)
    return d


def expiry_context(today: Optional[date] = None,
                   holidays: Optional[Set[str]] = None,
                   is_fno: bool = True,
                   grade: str = "A",
                   mode: str = "standard",
                   cfg: GradingConfig = GradingConfig) -> Dict:
    """
    F&O expiry-window flag + position-size multiplier for a candidate buy on
    `today`. Evaluate this against the REAL fill date (live, for OPEN positions),
    not at analysis time.

    Cash (non-F&O) stocks: expiry is irrelevant -> always clean, multiplier 1.0.
    Multiplier scales QUANTITY only; the structural SL from the plan never moves.

    Returns: {in_window, multiplier, expiry, days_to_expiry, reason}
    """
    today = today or datetime.now().date()
    holidays = holidays or set()

    if not is_fno:
        return {"in_window": False, "multiplier": 1.0, "expiry": None,
                "days_to_expiry": None, "reason": "Cash stock — expiry N/A"}

    exp = monthly_expiry(today.year, today.month, holidays, cfg)
    if today > exp:  # this month's expiry passed -> roll to next month
        ny = today.year + (today.month == 12)
        nm = (today.month % 12) + 1
        exp = monthly_expiry(ny, nm, holidays, cfg)

    back = cfg.CONSERVATIVE_BACK if mode == "conservative" else cfg.STANDARD_BACK_DAYS
    window = {exp}
    cur = exp
    for _ in range(back):
        cur = _step(cur, -1, holidays)
        window.add(cur)
    for _ in range(cfg.FORWARD_DAYS):
        window.add(_step(exp, +1, holidays))

    in_window = today in window
    days_to_expiry = (exp - today).days

    if not in_window:
        mult, reason = (0.5 if grade == "C" else 1.0), "Clean zone — normal size"
    elif grade == "A":
        mult, reason = cfg.WINDOW_MULTIPLIER, "Expiry window — A setup, half size"
    elif grade == "B":
        mult, reason = cfg.WINDOW_MULTIPLIER / 2, "Expiry window — B setup, quarter size"
    else:
        mult, reason = 0.0, "Expiry window — C setup, skip"

    return {"in_window": in_window, "multiplier": mult, "expiry": exp.isoformat(),
            "days_to_expiry": days_to_expiry, "reason": reason}


# ── Smoke test (mirrors the __main__ pattern in your other core modules) ─────
if __name__ == "__main__":
    tech = {"price": 500, "ema20": 490, "ema50": 470, "weekly_trend": "BULLISH",
            "base_status": "STABLE_BASE", "false_breakout_risk": "LOW",
            "volume_ratio": 1.8, "return_20d": 8.0, "adx": 28.0}
    plan = {"setup_type": "BREAKOUT", "rr_ratio": 3.0, "target_2": 545}

    g = grade_setup(tech, plan)
    print("Grade:", g["grade"], "Score:", g["score"], "Breakdown:", g["breakdown"])

    holidays = {"2026-01-26", "2026-03-21"}
    for d in ("2026-05-22", "2026-05-26", "2026-05-27"):
        ctx = expiry_context(today=date.fromisoformat(d), holidays=holidays,
                             is_fno=True, grade=g["grade"])
        print(f"{d}: in_window={ctx['in_window']} mult={ctx['multiplier']} "
              f"exp={ctx['expiry']} ({ctx['days_to_expiry']}d) — {ctx['reason']}")
