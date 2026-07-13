"""Pure R-multiple expectancy analytics (issue #5).

R = (exit − entry) / (entry − initial_SL): each trade's result measured in
units of its own initial risk. Expectancy = mean R. No Flask, no file I/O —
the server layer supplies closed-trade dicts and the post-mortem JSON map.

Historical rows never carry an Initial_SL column (the trailing logic mutates
Current_SL in place), so resolve_initial_sl() reconstructs the original stop
at read time without touching positions.csv.
"""

BUCKET_LOW = "<1.5"
BUCKET_MID = "1.5-2.5"
BUCKET_HIGH = ">2.5"
UNKNOWN = "UNKNOWN"


def _num(val):
    """Parse a CSV cell / dict value to float, or None."""
    if val is None:
        return None
    try:
        s = str(val).strip()
        if not s or s.lower() == "nan":
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def compute_trade_r(entry, exit_px, initial_sl):
    """R-multiple for one trade, or None when it cannot be computed honestly.

    Excludes (returns None) when any price is missing or the initial risk
    (entry − initial_SL) is zero/negative — never divide by a bad risk.
    """
    entry = _num(entry)
    exit_px = _num(exit_px)
    initial_sl = _num(initial_sl)
    if entry is None or exit_px is None or initial_sl is None:
        return None
    risk = entry - initial_sl
    if risk <= 0:
        return None
    return round((exit_px - entry) / risk, 4)


def resolve_initial_sl(row, pm_data):
    """Best-available original stop for a positions.csv row → (sl, source).

    Priority (first hit wins):
      1. Initial_SL column        → "exact"          (new trades, written at buy)
      2. post-mortem stop_price   → "pm_json"        (SL-loss autopsies)
      3. SL_LOSS with T1 never hit → Current_SL is still the original
                                  → "untriggered_sl"
      4. Risk_Per_Share           → entry − risk     → "risk_per_share"
      5. none of the above        → (None, "unrecoverable")
    """
    exact = _num(row.get("Initial_SL"))
    if exact is not None:
        return exact, "exact"

    sym = str(row.get("Symbol", "")).strip()
    entry_date = str(row.get("Entry_Date", "") or "").strip()
    pm_map = pm_data or {}
    pm = pm_map.get(f"{sym}_{entry_date}") or pm_map.get(sym)
    if pm:
        stop = _num((pm.get("price_path") or {}).get("stop_price"))
        if stop is not None:
            return stop, "pm_json"

    outcome = str(row.get("Outcome", "")).strip()
    t1_hit = str(row.get("T1_Hit_Date", "") or "").strip()
    if outcome == "SL_LOSS" and (not t1_hit or t1_hit.lower() == "nan"):
        cur_sl = _num(row.get("Current_SL"))
        if cur_sl is not None:
            return cur_sl, "untriggered_sl"

    rps = _num(row.get("Risk_Per_Share"))
    entry = _num(row.get("Entry_Price"))
    if rps is not None and rps > 0 and entry is not None:
        return round(entry - rps, 4), "risk_per_share"

    return None, "unrecoverable"


def vol_bucket(ratio):
    """Bucket an entry volume ratio; unparseable/missing → UNKNOWN."""
    r = _num(ratio)
    if r is None:
        return UNKNOWN
    if r < 1.5:
        return BUCKET_LOW
    if r <= 2.5:
        return BUCKET_MID
    return BUCKET_HIGH


def compute_slippage(trade):
    """Slippage on an SL exit: intended stop (Current_SL at close) vs actual fill.

    Returns None for non-SL exits or when inputs are missing. slippage_r is
    the extra loss as a fraction of the initial risk.
    """
    if str(trade.get("outcome", "")).strip() != "SL_LOSS":
        return None
    intended = _num(trade.get("current_sl"))
    exit_px = _num(trade.get("exit"))
    if intended is None or exit_px is None:
        return None
    rupees = round(intended - exit_px, 4)
    entry = _num(trade.get("entry"))
    initial_sl = _num(trade.get("initial_sl"))
    slip_r = None
    if entry is not None and initial_sl is not None and entry - initial_sl > 0:
        slip_r = round(rupees / (entry - initial_sl), 4)
    return {"slippage_rupees": rupees, "slippage_r": slip_r}


def _bucket_stats(pairs):
    """pairs: list of (bucket, r) → {bucket: {expectancy_r, count, wins, losses}}."""
    out = {}
    for bucket, r in pairs:
        b = out.setdefault(bucket, {"rs": [], "wins": 0, "losses": 0})
        b["rs"].append(r)
        if r > 0:
            b["wins"] += 1
        else:
            b["losses"] += 1
    return {
        k: {
            "expectancy_r": round(sum(v["rs"]) / len(v["rs"]), 3),
            "count": len(v["rs"]),
            "wins": v["wins"],
            "losses": v["losses"],
        }
        for k, v in out.items()
    }


def _label(val):
    s = str(val or "").strip()
    return s if s and s.lower() != "nan" else UNKNOWN


def compute_symbol_history(trades):
    """Per-symbol trade record over the same closed-trade dicts as
    compute_r_analytics → {symbol: {trades, wins, losses, avg_r, r_count}}.

    Win = money definition (exit > entry), independent of the Outcome label.
    Trades with unparseable prices count in `trades` but neither W nor L.
    avg_r averages only trades with a computable R (unrecoverable initial
    SLs are excluded, never estimated); r_count is how many contributed.
    """
    out = {}
    for t in trades:
        sym = str(t.get("symbol", "") or "").strip()
        if not sym:
            continue
        s = out.setdefault(sym, {"trades": 0, "wins": 0, "losses": 0,
                                 "rs": []})
        s["trades"] += 1
        entry = _num(t.get("entry"))
        exit_px = _num(t.get("exit"))
        if entry is not None and entry > 0 and exit_px is not None:
            if exit_px > entry:
                s["wins"] += 1
            else:
                s["losses"] += 1
        r = compute_trade_r(t.get("entry"), t.get("exit"), t.get("initial_sl"))
        if r is not None:
            s["rs"].append(r)
    return {
        sym: {
            "trades": s["trades"],
            "wins": s["wins"],
            "losses": s["losses"],
            "avg_r": round(sum(s["rs"]) / len(s["rs"]), 2) if s["rs"] else None,
            "r_count": len(s["rs"]),
        }
        for sym, s in out.items()
    }


def compute_r_analytics(trades):
    """Aggregate R expectancy + slippage over closed-trade dicts.

    Each trade dict: symbol, entry, exit, initial_sl, initial_sl_source,
    current_sl, outcome, setup, grade, sector, vol_ratio, nifty_regime.
    Trades whose R cannot be computed are excluded and counted.
    """
    rs = []            # (trade, r) with computable R
    excluded = 0
    for t in trades:
        r = compute_trade_r(t.get("entry"), t.get("exit"), t.get("initial_sl"))
        if r is None:
            excluded += 1
        else:
            rs.append((t, r))

    if not rs:
        return {
            "expectancy_r": None,
            "trades_with_r": 0,
            "trades_excluded_no_sl": excluded,
            "win_r_avg": None,
            "loss_r_avg": None,
            "r_distribution": [],
            "slippage": {
                "n_sl_exits": 0,
                "avg_slippage_rupees": None,
                "avg_slippage_r": None,
                "expectancy_r_slippage_adjusted": None,
            },
            "breakdowns": {},
        }

    r_vals = [r for _, r in rs]
    win_rs = [r for r in r_vals if r > 0]
    loss_rs = [r for r in r_vals if r <= 0]

    # Slippage: raw R already reflects the actual (slipped) fill; the adjusted
    # expectancy shows what expectancy would have been at the intended stop.
    slips = []
    no_slip_rs = []
    for t, r in rs:
        s = compute_slippage(t)
        if s is not None:
            slips.append(s)
            intended_r = compute_trade_r(t.get("entry"), t.get("current_sl"),
                                         t.get("initial_sl"))
            no_slip_rs.append(intended_r if intended_r is not None else r)
        else:
            no_slip_rs.append(r)
    slip_rupees = [s["slippage_rupees"] for s in slips]
    slip_rvals = [s["slippage_r"] for s in slips if s["slippage_r"] is not None]

    breakdowns = {
        "setup": _bucket_stats([(_label(t.get("setup")), r) for t, r in rs]),
        "grade": _bucket_stats([(_label(t.get("grade")), r) for t, r in rs]),
        "sector": _bucket_stats([(_label(t.get("sector")), r) for t, r in rs]),
        "vol_bucket": _bucket_stats([(vol_bucket(t.get("vol_ratio")), r) for t, r in rs]),
        "nifty_regime": _bucket_stats([(_label(t.get("nifty_regime")), r) for t, r in rs]),
    }

    return {
        "expectancy_r": round(sum(r_vals) / len(r_vals), 3),
        "trades_with_r": len(r_vals),
        "trades_excluded_no_sl": excluded,
        "win_r_avg": round(sum(win_rs) / len(win_rs), 3) if win_rs else None,
        "loss_r_avg": round(sum(loss_rs) / len(loss_rs), 3) if loss_rs else None,
        "r_distribution": sorted(r_vals),
        "slippage": {
            "n_sl_exits": len(slips),
            "avg_slippage_rupees": round(sum(slip_rupees) / len(slip_rupees), 4) if slip_rupees else None,
            "avg_slippage_r": round(sum(slip_rvals) / len(slip_rvals), 4) if slip_rvals else None,
            "expectancy_r_slippage_adjusted": round(sum(no_slip_rs) / len(no_slip_rs), 3),
        },
        "breakdowns": breakdowns,
    }
