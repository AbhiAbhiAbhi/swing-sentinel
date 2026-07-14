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
