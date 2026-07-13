"""Historical & Live Evidence engine (docs/STOCK_CARD_HISTORICAL_AND_LIVE_EVIDENCE_SPEC.md).

Replays the current strategy rules over a single symbol's daily OHLCV with
realistic execution — limit entries that may NOT fill, gap-through-stop exits
at the open, 50% out at T1 with the stop moved to breakeven, a 20-session time
exit, configurable Indian cash-market costs — and de-duplicates consecutive
signals into independent trade episodes.

Pure computation: no Flask, no file I/O (the store layer handles caching), and
the OHLCV dataframe is injectable so tests never touch yfinance. Historical
levels are derived only from bars at or before each signal day (no look-ahead).
"""
import hashlib
import json
import os
from datetime import datetime

import pandas as pd

try:
    from core.backtest import (
        DEFAULT_SIGNAL_FILTERS, fetch_history, signal_mask, _atr, _ema, _rsi,
    )
    from core.core_trade_plan import calculate_trade_plan
    from core.core_r_analytics import compute_trade_r, resolve_initial_sl, _num
except ImportError:
    from backtest import (
        DEFAULT_SIGNAL_FILTERS, fetch_history, signal_mask, _atr, _ema, _rsi,
    )
    from core_trade_plan import calculate_trade_plan
    from core_r_analytics import compute_trade_r, resolve_initial_sl, _num

SCHEMA_VERSION = 1
SETUP_FAMILIES = ("PULLBACK", "BREAKOUT", "SUPPORT_BOUNCE", "CONSOLIDATION")

SAMPLE_LABELS = (          # independent episodes → label (spec table)
    (30, "stronger"),
    (15, "usable"),
    (5, "weak"),
    (0, "insufficient"),
)

DEFAULT_RULES = {
    "entry_mode": "limit",
    "max_wait_sessions": 5,
    "max_hold_sessions": 20,
    "cooldown_sessions": 5,
    "same_bar_policy": "stop_first",
    "t1_exit_fraction": 0.5,
    "breakeven_after_t1": True,
    "cost_model_version": "india-cash-v1",
    "cost_model": {
        "buy_slippage_pct": 0.05,
        "sell_slippage_pct": 0.05,
        "brokerage_pct": 0.0,          # discount brokers: 0 on delivery
        "stt_pct": 0.1,                # delivery: 0.1% both sides
        "exchange_txn_pct": 0.00297,
        "sebi_pct": 0.0001,
        "gst_pct_on_charges": 18.0,
        "stamp_duty_buy_pct": 0.015,
    },
}


def load_rule_config(root=None):
    """scan_filters from data/schedule_config.json merged over DEFAULT_RULES.
    Missing/broken config file → engine defaults (still deterministic)."""
    cfg = {"signal_filters": dict(DEFAULT_SIGNAL_FILTERS)}
    cfg.update(json.loads(json.dumps(DEFAULT_RULES)))  # deep copy
    if root:
        path = os.path.join(root, "data", "schedule_config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                sf = (json.load(fh) or {}).get("scan_filters") or {}
            if sf.get("min_price") is not None:
                cfg["signal_filters"]["min_price"] = sf["min_price"]
            if sf.get("rsi_min") is not None:
                cfg["signal_filters"]["rsi_min"] = sf["rsi_min"]
            if sf.get("rsi_max") is not None:
                cfg["signal_filters"]["rsi_max"] = sf["rsi_max"]
            if sf.get("adx_min") is not None:
                cfg["signal_filters"]["adx_min"] = sf["adx_min"]
            if sf.get("min_volume_lakh") is not None:
                cfg["signal_filters"]["min_volume"] = int(sf["min_volume_lakh"] * 100_000)
        except Exception:
            pass
    return cfg


def strategy_version(cfg) -> str:
    """Deterministic version from the canonical rule config (spec §Strategy
    version): any rule change → new version → distinct cache files."""
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return "v1.0-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Point-in-time plan ───────────────────────────────────────────────────────

def plan_at(df, idx, ema20, ema50, atr, rsi):
    """calculate_trade_plan() inputs derived only from bars <= idx (S/R windows
    exclude the signal bar itself, matching backtest_symbol / live scan)."""
    window = df.iloc[: idx + 1]
    if len(window) < 60:
        return None
    close = df["Close"]
    stock_data = {
        "price":        float(close.iat[idx]),
        "ema20":        float(ema20.iat[idx]),
        "ema50":        float(ema50.iat[idx]),
        "support_1":    float(window["Low"].iloc[:-1].tail(20).min()),
        "resistance_1": float(window["High"].iloc[:-1].tail(20).max()),
        "resistance_2": float(window["High"].iloc[:-1].tail(60).max()),
        "atr":          float(atr.iat[idx]) if pd.notna(atr.iat[idx]) else float(close.iat[idx]) * 0.02,
        "rsi":          float(rsi.iat[idx]) if pd.notna(rsi.iat[idx]) else 50.0,
    }
    return calculate_trade_plan(stock_data)


# ── Costs ────────────────────────────────────────────────────────────────────

def apply_costs(fill_px, exits, cost_cfg):
    """Gross/net P&L% for one episode. `exits` = [(price, fraction)] with
    slippage already baked into the prices. Charges are % of traded notional
    per side; GST applies to brokerage + exchange + SEBI charges."""
    if not exits or not fill_px:
        return {"gross_pnl_pct": 0.0, "net_pnl_pct": 0.0, "cost_pct": 0.0}
    c = cost_cfg
    gross = sum(frac * (px - fill_px) / fill_px for px, frac in exits) * 100

    gstable_side = c["brokerage_pct"] + c["exchange_txn_pct"] + c["sebi_pct"]
    side_pct = gstable_side * (1 + c["gst_pct_on_charges"] / 100)
    buy_pct = side_pct + c["stt_pct"] + c["stamp_duty_buy_pct"]
    # Sell notional relative to buy notional scales with each exit price.
    sell_pct = sum((side_pct + c["stt_pct"]) * frac * px / fill_px
                   for px, frac in exits)
    cost_pct = buy_pct + sell_pct
    return {
        "gross_pnl_pct": round(gross, 4),
        "net_pnl_pct": round(gross - cost_pct, 4),
        "cost_pct": round(cost_pct, 4),
    }


# ── Episode simulation ───────────────────────────────────────────────────────

def simulate_episode(df, signal_idx, plan, rules):
    """One independent episode: limit-entry wait → fill or NOT_FILLED →
    stop/T1/T2/time exits with gap handling. Returns the episode record."""
    cm = rules["cost_model"]
    buy_slip = 1 + cm["buy_slippage_pct"] / 100
    sell_slip = 1 - cm["sell_slippage_pct"] / 100
    limit = plan["entry_zone_max"]
    sl = plan["stop_loss"]
    t1, t2 = plan["target_1"], plan["target_2"]
    opens, highs, lows, closes = df["Open"], df["High"], df["Low"], df["Close"]
    n = len(df)

    ep = {
        "signal_date": df.index[signal_idx].strftime("%Y-%m-%d"),
        "setup_type": plan["setup_type"],
        "entry_zone_min": round(plan["entry_zone_min"], 2),
        "entry_zone_max": round(limit, 2),
        "sl": round(sl, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "fill_status": "NOT_FILLED",
        "fill_date": None,
        "fill_price": None,
        "exits": [],
        "gross_r": None,
        "net_r": None,
        "hold_sessions": None,
        "gap_through_stop": False,
        "end_idx": min(signal_idx + rules["max_wait_sessions"], n - 1),
    }

    # ── Fill window: limit at entry_zone_max, never chase above it ─────────
    fill_idx = None
    for j in range(signal_idx + 1, min(signal_idx + 1 + rules["max_wait_sessions"], n)):
        if float(lows.iat[j]) <= limit:
            raw = min(float(opens.iat[j]), limit)   # gap-down opens fill at open
            fill_idx = j
            ep["fill_status"] = "FILLED"
            ep["fill_date"] = df.index[j].strftime("%Y-%m-%d")
            ep["fill_price"] = round(raw * buy_slip, 4)
            break
    if fill_idx is None:
        return ep

    fill_px = ep["fill_price"]
    stop = sl
    remaining = 1.0
    t1_done = False
    exits = []          # (price_after_slippage, fraction, reason, bar_idx)
    last_bar = min(fill_idx + rules["max_hold_sessions"], n - 1)

    for k in range(fill_idx, last_bar + 1):
        o, hi, lo = float(opens.iat[k]), float(highs.iat[k]), float(lows.iat[k])
        stop_reason = "BREAKEVEN" if t1_done and rules["breakeven_after_t1"] else "SL"
        # Stop first (conservative same-bar policy). Gap-through: open below stop
        # exits at the open, not the ideal stop price.
        if k > fill_idx and o <= stop:
            exits.append((o * sell_slip, remaining, stop_reason, k))
            if o < stop:
                ep["gap_through_stop"] = stop_reason == "SL"
            remaining = 0.0
            break
        if lo <= stop:
            exits.append((stop * sell_slip, remaining, stop_reason, k))
            remaining = 0.0
            break
        if not t1_done and hi >= t1:
            frac = rules["t1_exit_fraction"]
            exits.append((t1 * sell_slip, frac, "T1", k))
            remaining = round(remaining - frac, 6)
            t1_done = True
            if rules["breakeven_after_t1"]:
                stop = fill_px
        if remaining > 0 and hi >= t2:
            exits.append((t2 * sell_slip, remaining, "T2", k))
            remaining = 0.0
            break
    if remaining > 0:
        exits.append((float(closes.iat[last_bar]) * sell_slip, remaining, "TIME", last_bar))

    ep["exits"] = [
        {"date": df.index[b].strftime("%Y-%m-%d"), "price": round(px, 4),
         "fraction": frac, "reason": reason}
        for px, frac, reason, b in exits
    ]
    ep["hold_sessions"] = exits[-1][3] - fill_idx
    ep["end_idx"] = exits[-1][3]

    pnl = apply_costs(fill_px, [(px, frac) for px, frac, _, _ in exits], cm)
    ep.update(pnl)
    risk_pct = (fill_px - sl) / fill_px * 100
    if risk_pct > 0:
        ep["gross_r"] = round(pnl["gross_pnl_pct"] / risk_pct, 3)
        ep["net_r"] = round(pnl["net_pnl_pct"] / risk_pct, 3)
    return ep


def build_episodes(df, setup_type, rules, window_start_idx, filters=None,
                   signal_indices=None, plan_fn=None):
    """Signals → independent episodes: one active episode per setup family,
    later signals suppressed until end_idx + cooldown. Returns
    (episodes, signals_observed).

    signal_indices / plan_fn are injection points for unit tests; production
    callers leave them None (signals from signal_mask, plans from plan_at)."""
    # Leave room for the fill window + holding period so episodes complete
    # inside the data (mirrors the original backtester's 30-bar trim).
    last_signal_idx = len(df) - (rules["max_wait_sessions"] + rules["max_hold_sessions"] + 1)

    if signal_indices is None:
        mask = signal_mask(df, filters)
        signal_indices = [i for i in range(max(window_start_idx, 200),
                                           max(last_signal_idx, 0))
                          if bool(mask.iat[i])]
    else:
        signal_indices = [i for i in signal_indices
                          if window_start_idx <= i < last_signal_idx]

    if plan_fn is None:
        close = df["Close"]
        ema20, ema50 = _ema(close, 20), _ema(close, 50)
        atr, rsi = _atr(df, 14), _rsi(close, 14)
        plan_fn = lambda d, i: plan_at(d, i, ema20, ema50, atr, rsi)  # noqa: E731

    episodes = []
    signals_observed = 0
    blocked_until = -1
    for i in signal_indices:
        plan = plan_fn(df, i)
        if plan is None or plan["setup_type"] != setup_type:
            continue
        signals_observed += 1
        if i <= blocked_until:
            continue        # same market move — episode already active/cooling
        ep = simulate_episode(df, i, plan, rules)
        episodes.append(ep)
        blocked_until = ep["end_idx"] + rules["cooldown_sessions"]
    return episodes, signals_observed


def sample_label(n_episodes):
    for threshold, label in SAMPLE_LABELS:
        if n_episodes >= threshold:
            return label
    return "insufficient"


def _median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else round((vals[m - 1] + vals[m]) / 2, 3)


def summarize(episodes, signals_observed):
    filled = [e for e in episodes if e["fill_status"] == "FILLED"]
    rs = [e["net_r"] for e in filled if e["net_r"] is not None]
    wins = sum(1 for r in rs if r > 0)
    losses = sum(1 for r in rs if r <= 0)
    running, peak, mdd = 0.0, 0.0, 0.0
    for r in rs:
        running += r
        peak = max(peak, running)
        mdd = min(mdd, running - peak)
    return {
        "signals_observed": signals_observed,
        "independent_episodes": len(episodes),
        "filled": len(filled),
        "not_filled": len(episodes) - len(filled),
        "wins": wins,
        "losses": losses,
        "net_expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "median_realised_r": _median(rs),
        "median_hold_sessions": _median([e["hold_sessions"] for e in filled
                                         if e["hold_sessions"] is not None]),
        "worst_realised_r": min(rs) if rs else None,
        "gap_through_stop_count": sum(1 for e in filled if e["gap_through_stop"]),
        "max_drawdown_r": round(mdd, 3),
    }


def run_historical_evidence(symbol, setup_type, df=None, rules=None, root=None):
    """Full evidence result matching the spec cache schema. 12-month window
    first; <15 independent episodes → extend to 24 months in-memory (one
    yfinance fetch of 24 months up front, no second network call)."""
    cfg = rules or load_rule_config(root)
    sver = strategy_version(cfg)
    sim_rules = {k: cfg[k] for k in DEFAULT_RULES}
    filters = cfg.get("signal_filters")

    base = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "setup_type": setup_type,
        "strategy_version": sver,
        "generated_at": datetime.now().astimezone().isoformat(),
        "rules": {
            "entry_mode": sim_rules["entry_mode"],
            "max_wait_sessions": sim_rules["max_wait_sessions"],
            "max_hold_sessions": sim_rules["max_hold_sessions"],
            "cooldown_sessions": sim_rules["cooldown_sessions"],
            "same_bar_policy": sim_rules["same_bar_policy"],
            "t1_exit_fraction": sim_rules["t1_exit_fraction"],
            "breakeven_after_t1": sim_rules["breakeven_after_t1"],
            "cost_model_version": sim_rules["cost_model_version"],
        },
    }

    if df is None:
        df = fetch_history(symbol, 24)
    if df is None or df.empty or len(df) < 220:
        bars = 0 if df is None or df.empty else len(df)
        return {
            **base,
            "market_data_as_of": (df.index[-1].strftime("%Y-%m-%d")
                                  if bars else None),
            "coverage": {"start": (df.index[0].strftime("%Y-%m-%d") if bars else None),
                         "end": (df.index[-1].strftime("%Y-%m-%d") if bars else None),
                         "sessions": bars, "warmup_sessions": 0,
                         "sample_quality": "insufficient",
                         "coverage_notes": [
                             f"only {bars} daily bars available — too short for a "
                             "reliable simulation (recent listing or data gap)"]},
            "summary": summarize([], 0),
            "episodes": [],
            "status": "insufficient_history",
            "stale_reason": None,
        }

    months_used = 12
    window_start = max(200, len(df) - 12 * 22)
    episodes, signals = build_episodes(df, setup_type, sim_rules, window_start, filters)
    if len(episodes) < 15:
        window_24 = max(200, len(df) - 24 * 22)
        if window_24 < window_start:
            months_used = 24
            episodes, signals = build_episodes(df, setup_type, sim_rules, window_24, filters)
            window_start = window_24

    notes = []
    if months_used == 24:
        notes.append("extended to 24 months (fewer than 15 independent episodes in 12)")
    notes.append("earnings calendar, news sentiment and institutional filters "
                 "not_reconstructed for historical dates")

    return {
        **base,
        "market_data_as_of": df.index[-1].strftime("%Y-%m-%d"),
        "coverage": {
            "start": df.index[window_start].strftime("%Y-%m-%d"),
            "end": df.index[-1].strftime("%Y-%m-%d"),
            "months": months_used,
            "sessions": int(len(df) - window_start),
            "warmup_sessions": int(window_start),
            "sample_quality": sample_label(len(episodes)),
            "coverage_notes": notes,
        },
        "summary": summarize(episodes, signals),
        "episodes": [{k: v for k, v in e.items() if k != "end_idx"}
                     for e in episodes],
        "status": "complete",
        "stale_reason": None,
    }


# ── Live Swing Sentinel record ───────────────────────────────────────────────

def build_live_evidence(symbol, setup_type, rows, pm_data, sver):
    """Actual app record from closed positions.csv rows (read-only; initial
    SLs resolved at read time, unrecoverable trades excluded from R — never
    estimated). All existing history predates strategy versioning, so
    strategy_compatible is a label, not a filter."""
    sym = str(symbol).strip().upper()
    matched = []
    for row in rows:
        if str(row.get("Symbol", "")).strip().upper() != sym:
            continue
        if str(row.get("Setup", "")).strip().upper() != str(setup_type).strip().upper():
            continue
        matched.append(row)

    rs, complete, partial = [], 0, 0
    holds, worst, gaps = [], None, 0
    win_n = 0
    for row in matched:
        initial_sl, src = resolve_initial_sl(row, pm_data)
        entry = _num(row.get("Entry_Price"))
        exit_px = _num(row.get("Closing_Price"))
        r = compute_trade_r(entry, exit_px, initial_sl)
        if src == "exact" and _num(row.get("Buy_RSI")) is not None:
            complete += 1
        else:
            partial += 1
        if entry and exit_px and entry > 0 and exit_px > entry:
            win_n += 1
        if r is not None:
            rs.append(r)
            worst = r if worst is None else min(worst, r)
        try:
            e = datetime.strptime(str(row.get("Entry_Date", "")), "%Y-%m-%d")
            x_raw = str(row.get("T2_Hit_Date") or row.get("SL_Hit_Date") or "").strip()
            if x_raw and x_raw.lower() != "nan":
                holds.append((datetime.strptime(x_raw, "%Y-%m-%d") - e).days)
        except Exception:
            pass

    n = len(matched)
    status = "ok" if n >= 5 else ("low_confidence" if n >= 1 else "no_data")
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": sym,
        "setup_type": setup_type,
        "strategy_version": sver,
        "generated_at": datetime.now().astimezone().isoformat(),
        "matching": {
            "same_symbol": True,
            "same_setup": True,
            "strategy_compatible": False,   # pre-versioning history
            "macro_match": "not_available",
        },
        "summary": {
            "completed": n,
            "complete_snapshots": complete if n else 0,
            "partial_legacy_records": partial if n else 0,
            "net_expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
            "r_sample": len(rs),
            "win_rate": round(win_n / n, 3) if n else None,
            "median_hold_sessions": _median(holds),
            "worst_realised_r": worst,
            "gap_through_stop_count": gaps,
        },
        "status": status,
    }
