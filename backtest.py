"""
Historical Backtest — replay the Chartink rules + trade-plan logic over
the last 6 months of NSE data and compute win rate per setup type.

Reuses:  calculate_trade_plan() from core_trade_plan.py — identical logic
         to live trading so results are apples-to-apples.

Output:  data/backtest_results.json  (same shape as GET /api/results)

Usage:   python backtest.py
         python backtest.py --months 12 --universe nifty100
"""
import argparse
import json
import logging
import os
from datetime import datetime

import pandas as pd
import yfinance as yf

from core_trade_plan import calculate_trade_plan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("backtest")

# ── Universes ──────────────────────────────────────────────────────────────

NIFTY50 = [
    "RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS","BHARTIARTL","ITC","LT","KOTAKBANK",
    "AXISBANK","SBIN","HINDUNILVR","BAJFINANCE","MARUTI","ASIANPAINT","TITAN","NESTLEIND",
    "SUNPHARMA","ULTRACEMCO","NTPC","POWERGRID","HCLTECH","ADANIENT","WIPRO","JSWSTEEL",
    "M&M","TATAMOTORS","ONGC","COALINDIA","TECHM","INDUSINDBK","BPCL","BAJAJ-AUTO",
    "GRASIM","EICHERMOT","HEROMOTOCO","HINDALCO","SHRIRAMFIN","ADANIPORTS","BAJAJFINSV",
    "DIVISLAB","DRREDDY","CIPLA","APOLLOHOSP","BRITANNIA","TATACONSUM","SBILIFE","HDFCLIFE",
    "LTIM","TATASTEEL",
]

NIFTY_NEXT50 = [
    "ADANIGREEN","ADANIPOWER","AMBUJACEM","BAJAJHLDNG","BANKBARODA","BERGEPAINT","BOSCHLTD",
    "CANBK","CHOLAFIN","COLPAL","DABUR","DLF","GAIL","GODREJCP","HAVELLS","ICICIGI",
    "ICICIPRULI","IOC","INDIGO","JINDALSTEL","LICI","NAUKRI","PIDILITIND","PNB","SAIL",
    "SIEMENS","SRF","TVSMOTOR","TORNTPHARM","UNITDSPR","VEDL","ZYDUSLIFE","ADANIENSOL",
    "ATGL","CGPOWER","DMART","HAL","IRCTC","JIOFIN","LODHA","MAKEMYTRIP","MOTHERSON",
    "PFC","RECLTD","TATAPOWER","TRENT","ZOMATO","BHEL","IRFC","NMDC",
]

UNIVERSES = {
    "nifty50":  NIFTY50,
    "nifty100": NIFTY50 + NIFTY_NEXT50,
}

# ── Indicators (vectorized, return full series) ─────────────────────────────

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).astype(float)

def _macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    ema_f = s.ewm(span=fast, adjust=False).mean()
    ema_s = s.ewm(span=slow, adjust=False).mean()
    line  = ema_f - ema_s
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    up   = hi.diff()
    down = -lo.diff()
    plus_dm  = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di  = 100 * plus_dm.rolling(period).mean() / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.rolling(period).mean()


# ── Backtest core ───────────────────────────────────────────────────────────

def fetch_history(symbol: str, months: int) -> pd.DataFrame:
    ticker = f"{symbol}.NS"
    # Need extra runway for 200-day EMA + look-ahead window (~30 days)
    period_days = months * 22 + 250
    try:
        df = yf.Ticker(ticker).history(period=f"{period_days}d")
        df = df.dropna(subset=["Close"])
        return df
    except Exception as exc:
        logger.warning("fetch %s failed: %s", symbol, exc)
        return pd.DataFrame()


def find_signals(df: pd.DataFrame, lookback_start_idx: int) -> list:
    """Walk forward through df, return list of indices where Chartink rules match."""
    close, vol = df["Close"], df["Volume"]
    ema20  = _ema(close, 20)
    ema50  = _ema(close, 50)
    ema200 = _ema(close, 200)
    rsi    = _rsi(close, 14)
    macd_line, macd_sig = _macd(close)
    adx    = _adx(df, 14)
    avg_vol = vol.rolling(20).mean()

    signals = []
    for i in range(lookback_start_idx, len(df) - 30):  # leave 30-day exit window
        try:
            if (
                close.iat[i] >= 50
                and close.iat[i] > ema20.iat[i]
                and ema20.iat[i] > ema50.iat[i]
                and close.iat[i] > ema200.iat[i]
                and 40 < rsi.iat[i] < 70
                and macd_line.iat[i] > macd_sig.iat[i]
                and adx.iat[i] > 20
                and vol.iat[i] > 500_000
                and vol.iat[i] > avg_vol.iat[i]   # extra: today's vol above 20d avg
            ):
                signals.append(i)
        except Exception:
            continue
    return signals


def simulate_outcome(df: pd.DataFrame, signal_idx: int, plan: dict) -> dict:
    """
    Walk forward from signal_idx+1 until T2 or SL hits (or 30-day timeout).
    Use intraday High/Low for crossing detection (more realistic than close-only).
    """
    t1 = plan["target_1"]
    t2 = plan["target_2"]
    sl = plan["stop_loss"]
    entry = (plan["entry_zone_min"] + plan["entry_zone_max"]) / 2

    for j in range(signal_idx + 1, min(signal_idx + 31, len(df))):
        hi, lo = df["High"].iat[j], df["Low"].iat[j]
        # SL takes priority if both hit on same day (conservative)
        if lo <= sl:
            return {
                "outcome": "SL_LOSS", "exit_price": sl,
                "days_held": j - signal_idx,
                "pnl_pct": round((sl - entry) / entry * 100, 2),
            }
        if hi >= t2:
            return {
                "outcome": "T2_WIN", "exit_price": t2,
                "days_held": j - signal_idx,
                "pnl_pct": round((t2 - entry) / entry * 100, 2),
            }

    # Timeout — close at last available close
    last_close = float(df["Close"].iat[min(signal_idx + 30, len(df) - 1)])
    return {
        "outcome": "TIMEOUT", "exit_price": last_close,
        "days_held": 30,
        "pnl_pct": round((last_close - entry) / entry * 100, 2),
    }


def detect_setup(close, ema20, ema50, support_1, resistance_1, idx) -> str:
    """Mirror calculate_trade_plan's setup labelling."""
    p, e20, e50 = close.iat[idx], ema20.iat[idx], ema50.iat[idx]
    if e20 > 0 and e20 > e50 and p >= e20:
        return "PULLBACK"
    if resistance_1 > 0 and p > resistance_1:
        return "BREAKOUT"
    if support_1 > 0 and p <= support_1 * 1.02:
        return "SUPPORT_BOUNCE"
    return "CONSOLIDATION"


def backtest_symbol(symbol: str, months: int) -> list:
    df = fetch_history(symbol, months)
    if df.empty or len(df) < 220:
        logger.info("skip %s — insufficient history (%d bars)", symbol, len(df))
        return []

    # Compute indicators needed for detect_setup / plan
    close = df["Close"]
    ema20, ema50 = _ema(close, 20), _ema(close, 50)
    atr  = _atr(df, 14)

    lookback_idx = max(200, len(df) - months * 22)
    signals = find_signals(df, lookback_idx)
    logger.info("%s: %d bars, %d signals", symbol, len(df), len(signals))

    trades = []
    for s_idx in signals:
        # Build stock_data the same way fetch_stock_technicals does
        window = df.iloc[: s_idx + 1]
        if len(window) < 60:
            continue
        stock_data = {
            "price":        float(close.iat[s_idx]),
            "ema20":        float(ema20.iat[s_idx]),
            "ema50":        float(ema50.iat[s_idx]),
            "support_1":    float(window["Low"].tail(20).min()),
            "resistance_1": float(window["High"].tail(20).max()),
            "resistance_2": float(window["High"].tail(60).max()),
            "atr":          float(atr.iat[s_idx]) if pd.notna(atr.iat[s_idx]) else float(close.iat[s_idx]) * 0.02,
        }
        plan    = calculate_trade_plan(stock_data)
        outcome = simulate_outcome(df, s_idx, plan)

        signal_date = df.index[s_idx].strftime("%Y-%m-%d")
        exit_idx    = min(s_idx + outcome["days_held"], len(df) - 1)
        exit_date   = df.index[exit_idx].strftime("%Y-%m-%d")

        trades.append({
            "symbol":     symbol,
            "setup":      plan["setup_type"],
            "entry_date": signal_date,
            "exit_date":  exit_date,
            "entry":      round((plan["entry_zone_min"] + plan["entry_zone_max"]) / 2, 2),
            "exit":       round(outcome["exit_price"], 2),
            "pnl_pct":    outcome["pnl_pct"],
            "days_held":  outcome["days_held"],
            "outcome":    outcome["outcome"],
        })
    return trades


def aggregate(all_trades: list) -> dict:
    """Match the shape of /api/results so the dashboard can use the same renderer."""
    total  = len(all_trades)
    wins   = sum(1 for t in all_trades if t["outcome"] == "T2_WIN")
    losses = sum(1 for t in all_trades if t["outcome"] == "SL_LOSS")
    closed = wins + losses
    win_rate = round(wins / closed, 3) if closed else 0
    held = [t["days_held"] for t in all_trades if t["outcome"] != "TIMEOUT"]
    avg_days_held = round(sum(held) / len(held), 1) if held else 0

    by_setup = {}
    for t in all_trades:
        s = t["setup"]
        b = by_setup.setdefault(s, {"total":0,"closed":0,"wins":0,"losses":0,"pnls":[]})
        b["total"] += 1
        if t["outcome"] == "T2_WIN":
            b["wins"]   += 1; b["closed"] += 1
        elif t["outcome"] == "SL_LOSS":
            b["losses"] += 1; b["closed"] += 1
        b["pnls"].append(t["pnl_pct"])
    for s, b in by_setup.items():
        b["win_rate"]    = round(b["wins"] / b["closed"], 3) if b["closed"] else 0
        b["avg_pnl_pct"] = round(sum(b["pnls"]) / len(b["pnls"]), 2) if b["pnls"] else 0
        del b["pnls"]

    # Show 50 most recent closed trades for the table
    closed_trades = [t for t in all_trades if t["outcome"] in ("T2_WIN", "SL_LOSS")]
    closed_trades.sort(key=lambda x: x["exit_date"], reverse=True)
    closed_positions = [
        {**t, "name": t["symbol"]} for t in closed_trades[:50]
    ]

    return {
        "total":            total,
        "open":             0,
        "closed":           closed,
        "wins":             wins,
        "losses":           losses,
        "win_rate":         win_rate,
        "avg_days_held":    avg_days_held,
        "by_setup":         by_setup,
        "closed_positions": closed_positions,
        "generated_at":     datetime.now().isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months",   type=int, default=6, help="Lookback in months (default 6)")
    ap.add_argument("--universe", type=str, default="nifty50", choices=list(UNIVERSES.keys()))
    args = ap.parse_args()

    symbols = UNIVERSES[args.universe]
    logger.info("Backtesting %d stocks over %d months …", len(symbols), args.months)

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        logger.info("[%d/%d] %s", i, len(symbols), sym)
        try:
            all_trades.extend(backtest_symbol(sym, args.months))
        except Exception as exc:
            logger.warning("%s skipped: %s", sym, exc)

    result = aggregate(all_trades)
    os.makedirs("data", exist_ok=True)
    out_path = "data/backtest_results.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("=" * 50)
    logger.info("Backtest complete — saved to %s", out_path)
    logger.info("Total signals: %d", result["total"])
    logger.info("Closed: %d  |  Wins: %d  |  Losses: %d  |  Win rate: %.1f%%",
                result["closed"], result["wins"], result["losses"], result["win_rate"] * 100)
    logger.info("Avg days held: %.1f", result["avg_days_held"])
    logger.info("By setup:")
    for s, b in result["by_setup"].items():
        logger.info("  %-15s  %d trades  %.1f%% WR  avg %.2f%%",
                    s, b["total"], b["win_rate"] * 100, b["avg_pnl_pct"])


if __name__ == "__main__":
    main()
