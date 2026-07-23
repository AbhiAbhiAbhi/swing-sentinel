"""
Counterfactual analytics for PRUNED watchlist candidates (issue #6).

For each Status=PRUNED row we measure what the trade would have done after
Prune_Date against its original plan (Target_1 / Target_2 / stop), so
prune/filter rules can be tuned with evidence instead of intuition:
  - CF_Return_10d/20d/30d : % return from the first close on/after Prune_Date
  - CF_Would_Have_Hit     : first-touch outcome within 30 days — T2/T1/SL/NONE
                            (SL wins a same-day tie: conservative), or
                            UNRESOLVED when dates/levels/bars are unusable.

Pure module: price bars are injected, no yfinance / Flask / file I/O here
(mirrors core_r_analytics.py so it stays unit-testable offline).
"""

import math
import re

CF_HORIZONS = (10, 20, 30)
CF_COLUMNS = ["CF_Return_10d", "CF_Return_20d", "CF_Return_30d",
              "CF_Would_Have_Hit", "CF_Computed_Date"]


def _to_float(val):
    try:
        f = float(val)
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


def compute_cf_for_row(row: dict, bars) -> dict:
    """
    Compute counterfactual fields for one pruned row.

    row  : dict of the positions.csv row (needs Target_1/Target_2 and
           Current_SL or Initial_SL).
    bars : pd.DataFrame of daily OHLC starting at/after Prune_Date, ascending,
           with High/Low/Close columns (index = session dates).

    Returns a dict with keys CF_Return_10d/20d/30d (float % or "" when the
    horizon hasn't matured yet), CF_Would_Have_Hit, and CF_Complete (bool:
    True when the 30d horizon existed so the row never needs recompute).
    """
    out = {f"CF_Return_{h}d": "" for h in CF_HORIZONS}
    out["CF_Would_Have_Hit"] = "UNRESOLVED"
    out["CF_Complete"] = False

    t1 = _to_float(row.get("Target_1"))
    t2 = _to_float(row.get("Target_2"))
    sl = _to_float(row.get("Current_SL")) or _to_float(row.get("Initial_SL"))

    if bars is None or len(bars) == 0:
        return out
    closes = bars["Close"].dropna()
    if closes.empty:
        return out
    base = float(closes.iloc[0])
    if not (math.isfinite(base) and base > 0):
        return out

    # Returns at trading-session horizons (bar N after the prune-day bar).
    for h in CF_HORIZONS:
        if len(closes) > h:
            out[f"CF_Return_{h}d"] = round((float(closes.iloc[h]) - base) / base * 100.0, 2)
    out["CF_Complete"] = len(closes) > max(CF_HORIZONS)

    # First-touch outcome over the 30-session window (skip the prune-day bar
    # itself — the prune decision was made on that bar's information).
    if t1 is None and sl is None:
        return out
    hit = "NONE"
    window = bars.iloc[1:max(CF_HORIZONS) + 1]
    for _, bar in window.iterrows():
        hi, lo = bar.get("High"), bar.get("Low")
        if hi is None or lo is None or not (math.isfinite(hi) and math.isfinite(lo)):
            continue
        if sl is not None and lo <= sl:
            hit = "SL"       # conservative: SL wins a same-day tie
            break
        if t2 is not None and hi >= t2:
            hit = "T2"
            break
        if t1 is not None and hi >= t1:
            hit = "T1"
            break
    out["CF_Would_Have_Hit"] = hit
    return out


# ── Prune_Reason bucketing ──────────────────────────────────────────────────

_REASON_BUCKETS = [
    (re.compile(r"safety gates|failed safety", re.I), "Safety gates failed"),
    (re.compile(r"structur", re.I),                   "Trend structure broke"),
    (re.compile(r"false breakout", re.I),             "False breakout risk"),
    (re.compile(r"absent from", re.I),                "Absent from data feed"),
    (re.compile(r"stale|days? old|time.based|expiry|expired", re.I), "Time-based expiry"),
    (re.compile(r"error during analysis", re.I),      "Analysis error"),
]


def bucket_prune_reason(reason) -> str:
    text = str(reason or "").strip()
    if not text or text.lower() in ("nan", "none"):
        return "Unspecified"
    for rx, label in _REASON_BUCKETS:
        if rx.search(text):
            return label
    return "Other"


def aggregate_cf_by_reason(rows: list) -> dict:
    """
    Aggregate CF fields per Prune_Reason bucket over PRUNED row dicts.

    Returns {"buckets": [...], "total": n, "resolved": n}; each bucket has
    count, resolved, avg_return_{10,20,30}d, hit-rate percentages
    (t_hit_pct = T1 or T2, sl_hit_pct, none_pct) and a verdict string.
    """
    buckets = {}
    total = resolved_total = 0
    for row in rows:
        total += 1
        b = buckets.setdefault(bucket_prune_reason(row.get("Prune_Reason")), {
            "count": 0, "resolved": 0,
            "returns": {h: [] for h in CF_HORIZONS},
            "hits": {"T1": 0, "T2": 0, "SL": 0, "NONE": 0},
        })
        b["count"] += 1
        hit = str(row.get("CF_Would_Have_Hit") or "").upper()
        if hit not in ("T1", "T2", "SL", "NONE"):
            continue
        b["resolved"] += 1
        resolved_total += 1
        b["hits"][hit] += 1
        for h in CF_HORIZONS:
            v = row.get(f"CF_Return_{h}d")
            try:
                f = float(v)
                if math.isfinite(f):
                    b["returns"][h].append(f)
            except (TypeError, ValueError):
                pass

    out = []
    for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["count"]):
        res = b["resolved"]
        entry = {"reason": name, "count": b["count"], "resolved": res}
        for h in CF_HORIZONS:
            vals = b["returns"][h]
            entry[f"avg_return_{h}d"] = round(sum(vals) / len(vals), 2) if vals else None
        t_hits = b["hits"]["T1"] + b["hits"]["T2"]
        entry["t_hit_pct"] = round(t_hits / res * 100.0, 1) if res else None
        entry["sl_hit_pct"] = round(b["hits"]["SL"] / res * 100.0, 1) if res else None
        entry["none_pct"] = round(b["hits"]["NONE"] / res * 100.0, 1) if res else None
        if res < 5:
            entry["verdict"] = "INSUFFICIENT_DATA"
        elif entry["t_hit_pct"] >= 50.0:
            entry["verdict"] = "PRUNING_WINNERS"   # rule may be costing money — loosen
        elif entry["sl_hit_pct"] >= 50.0:
            entry["verdict"] = "FILTER_HAS_ALPHA"  # prunes kept falling — keep/tighten
        else:
            entry["verdict"] = "NEUTRAL"
        out.append(entry)

    return {"buckets": out, "total": total, "resolved": resolved_total}


# ── Per-gate CF analytics ──────────────────────────────────────────────────

GATE_REGISTRY = {
    "weekly_trend":              "Weekly Trend",
    "fundamental_strength":      "Fundamental Strength",
    "institutional_dealings":    "FII/DII Dealings",
    "low_liquidity":             "Low Liquidity",
    "overextended_1m":           "Overextended 1M",
    "debate_skip_check":         "Debate Chamber",
    "data_freshness":            "Data Freshness",
    "ipo_age":                   "IPO Age",
    "earnings_soon":             "Earnings Proximity",
    "post_earnings_cooling":     "Post-Earnings Cooling",
    "sector_nifty_regime":       "Sector/Nifty Regime",
    "trend_distance_alignment":  "Trend-Distance Alignment",
    "recent_crash":              "Recent Crash",
    "no_mans_land":              "No Man's Land",
    "volatility":                "Volatility",
    "distance_to_entry":         "Distance to Entry",
    "cmp_risk_reward":           "CMP R:R",
    "breakout_volume":           "Breakout Volume",
    "unsustained_volume_spike":  "Unsustained Volume",
    "liquidity_trap":            "Liquidity Trap",
    "trend_stalling":            "Trend Stalling",
    "target_above_52w_high":     "Target > 52W High",
    "volume_vacuum_at_highs":    "Volume Vacuum",
    "fresh_bearish_macd":        "Bearish MACD",
    "momentum_divergence":       "Momentum Divergence",
    "supply_wall_congestion":    "Supply Wall",
    "multi_flag":                "Multi-Flag Rejection",
}

_GATE_FALLBACK_PATTERNS = [
    (re.compile(r"weekly.trend", re.I),           "weekly_trend"),
    (re.compile(r"fundamental", re.I),            "fundamental_strength"),
    (re.compile(r"FII|DII|institutional", re.I),  "institutional_dealings"),
    (re.compile(r"liquidity.trap", re.I),         "liquidity_trap"),
    (re.compile(r"low.liquid", re.I),             "low_liquidity"),
    (re.compile(r"overextend", re.I),             "overextended_1m"),
    (re.compile(r"debate", re.I),                 "debate_skip_check"),
    (re.compile(r"fresh|stale", re.I),            "data_freshness"),
    (re.compile(r"IPO|listing", re.I),            "ipo_age"),
    (re.compile(r"earning", re.I),                "earnings_soon"),
    (re.compile(r"post.earning|cooling", re.I),   "post_earnings_cooling"),
    (re.compile(r"sector|regime", re.I),          "sector_nifty_regime"),
    (re.compile(r"no.man", re.I),                 "no_mans_land"),
    (re.compile(r"volatil|ATR", re.I),            "volatility"),
    (re.compile(r"distance", re.I),               "distance_to_entry"),
    (re.compile(r"R:R|risk.reward", re.I),        "cmp_risk_reward"),
    (re.compile(r"breakout.vol", re.I),           "breakout_volume"),
    (re.compile(r"unsustain", re.I),              "unsustained_volume_spike"),
    (re.compile(r"trend.stall", re.I),            "trend_stalling"),
    (re.compile(r"52.?w", re.I),                  "target_above_52w_high"),
    (re.compile(r"volume.vacuum", re.I),          "volume_vacuum_at_highs"),
    (re.compile(r"MACD|bearish.macd", re.I),      "fresh_bearish_macd"),
    (re.compile(r"divergen", re.I),               "momentum_divergence"),
    (re.compile(r"supply.wall|congestion", re.I), "supply_wall_congestion"),
    (re.compile(r"multi.flag|multiple", re.I),    "multi_flag"),
    (re.compile(r"crash", re.I),                  "recent_crash"),
]


def bucket_by_gate_id(row) -> str:
    gate_id = str(row.get("Park_Gate_Id") or row.get("park_gate_id") or "").strip()
    if gate_id and gate_id in GATE_REGISTRY:
        return gate_id
    reason = str(row.get("Prune_Reason") or row.get("Park_Reason") or "")
    for rx, gid in _GATE_FALLBACK_PATTERNS:
        if rx.search(reason):
            return gid
    return "unknown"


def compute_gate_verdicts(rows: list) -> dict:
    """
    Per-gate breakdown across PRUNED + PARKED rows.

    Returns {"gates": [...], "total": n} where each gate entry has:
    count, parked, pruned, unparked, avg_return_10d,
    t_hit_pct, sl_hit_pct, verdict.
    """
    gates = {}
    total = 0
    for row in rows:
        total += 1
        gid = bucket_by_gate_id(row)
        g = gates.setdefault(gid, {
            "gate_id": gid,
            "label": GATE_REGISTRY.get(gid, gid),
            "count": 0, "parked": 0, "pruned": 0, "unparked": 0,
            "returns_10d": [],
            "hits": {"T1": 0, "T2": 0, "SL": 0, "NONE": 0},
            "resolved": 0,
        })
        g["count"] += 1

        status = str(row.get("Status") or "").upper()
        if status == "PARKED":
            g["parked"] += 1
        elif status == "PRUNED":
            g["pruned"] += 1
        unpark_date = str(row.get("Unpark_Date") or "").strip()
        if unpark_date:
            g["unparked"] += 1

        hit = str(row.get("CF_Would_Have_Hit") or "").upper()
        if hit in ("T1", "T2", "SL", "NONE"):
            g["resolved"] += 1
            g["hits"][hit] += 1

        v10 = row.get("CF_Return_10d")
        try:
            f = float(v10)
            if math.isfinite(f):
                g["returns_10d"].append(f)
        except (TypeError, ValueError):
            pass

    out = []
    for gid, g in sorted(gates.items(), key=lambda kv: -kv[1]["count"]):
        res = g["resolved"]
        t_hits = g["hits"]["T1"] + g["hits"]["T2"]
        entry = {
            "gate_id":     gid,
            "label":       g["label"],
            "count":       g["count"],
            "parked":      g["parked"],
            "pruned":      g["pruned"],
            "unparked":    g["unparked"],
            "resolved":    res,
            "avg_return_10d": round(sum(g["returns_10d"]) / len(g["returns_10d"]), 2) if g["returns_10d"] else None,
            "t_hit_pct":   round(t_hits / res * 100.0, 1) if res else None,
            "sl_hit_pct":  round(g["hits"]["SL"] / res * 100.0, 1) if res else None,
        }
        if res < 3:
            entry["verdict"] = "INSUFFICIENT_DATA"
        elif entry["t_hit_pct"] >= 50.0:
            entry["verdict"] = "PRUNING_WINNERS"
        elif entry["sl_hit_pct"] >= 50.0:
            entry["verdict"] = "FILTER_HAS_ALPHA"
        else:
            entry["verdict"] = "NEUTRAL"
        out.append(entry)

    return {"gates": out, "total": total}
